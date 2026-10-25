"""Bluesky and Misskey integration used by the playback timeline."""

import base64
import binascii
import ipaddress
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AppSetting

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20
SOCIAL_SETTING_KEYS = {"bluesky": "social.bluesky", "misskey": "social.misskey"}
SOCIAL_ACCOUNTS_KEY = "social.accounts"
MIAUTH_FLOW_TTL_SECONDS = 600
MIAUTH_FLOW_RETENTION_SECONDS = 60
MAX_MIAUTH_FLOWS = 32
MIAUTH_PERMISSIONS = ("read:account", "read:notes", "write:notes", "write:drive")
_MIAUTH_FLOWS: dict[str, dict] = {}
_MIAUTH_LOCK = threading.Lock()


class SocialServiceError(Exception):
    """An external service failure with a safe, user-facing message."""


def _fernet() -> Fernet:
    directory = Path(settings.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / "social-credentials.key"
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        key = Fernet.generate_key()
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(key)
                file.flush()
                os.fsync(file.fileno())
        except FileExistsError:
            key = key_path.read_bytes()
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise SocialServiceError("SNS 認証情報を復号できません。設定を再登録してください")


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not isinstance(value, str):
        raise SocialServiceError("SNS 認証情報を復号できません。設定を再登録してください")
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        raise SocialServiceError("SNS 認証情報を復号できません。設定を再登録してください")


def _legacy_account_id(provider: str, value: dict) -> str:
    if provider == "bluesky":
        identity = value.get("did") or value.get("identifier", "")
    else:
        identity = "|".join((
            str(value.get("instance_url") or "").casefold(),
            str(value.get("username") or "").casefold(),
            str(value.get("host") or "").casefold(),
        ))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-live-archiver:social:{provider}:{identity}").hex


def _accounts(db: Session) -> list[dict]:
    row = db.get(AppSetting, SOCIAL_ACCOUNTS_KEY)
    stored = row.value.get("value") if row and isinstance(row.value, dict) else None
    if isinstance(stored, list):
        return [dict(account) for account in stored if isinstance(account, dict)]

    # Keep existing single-account settings usable until the first account edit.
    accounts = []
    for provider, key in SOCIAL_SETTING_KEYS.items():
        legacy = db.get(AppSetting, key)
        value = legacy.value.get("value") if legacy and isinstance(legacy.value, dict) else None
        if isinstance(value, dict):
            accounts.append({**value, "id": _legacy_account_id(provider, value), "provider": provider})
    return accounts


def _save_accounts(db: Session, accounts: list[dict]) -> None:
    row = db.get(AppSetting, SOCIAL_ACCOUNTS_KEY)
    value = {"value": accounts}
    if row is None:
        db.add(AppSetting(key=SOCIAL_ACCOUNTS_KEY, value=value))
    else:
        row.value = value
    for key in SOCIAL_SETTING_KEYS.values():
        legacy = db.get(AppSetting, key)
        if legacy is not None:
            db.delete(legacy)
    db.commit()


def _account_identity(account: dict) -> tuple:
    provider = account.get("provider")
    if provider == "bluesky":
        return (provider, str(account.get("did") or account.get("identifier") or "").casefold())
    return (
        provider,
        str(account.get("instance_url") or "").casefold(),
        str(account.get("username") or "").casefold(),
        str(account.get("host") or "").casefold(),
    )


def _store_account(db: Session, account: dict) -> dict:
    accounts = _accounts(db)
    identity = _account_identity(account)
    existing = next((item for item in accounts if _account_identity(item) == identity), None)
    if existing:
        account["id"] = existing["id"]
        accounts = [account if item["id"] == existing["id"] else item for item in accounts]
    else:
        account["id"] = uuid.uuid4().hex
        accounts.append(account)
    _save_accounts(db, accounts)
    return account


def _save_account(db: Session, account: dict) -> None:
    accounts = _accounts(db)
    for index, existing in enumerate(accounts):
        if existing.get("id") == account.get("id"):
            accounts[index] = account
            _save_accounts(db, accounts)
            return


def _setting(db: Session, provider: str) -> dict | None:
    return next((account for account in _accounts(db) if account.get("provider") == provider), None)


def _validated_instance_url(value: str) -> str:
    parsed = urlparse(value.strip().rstrip("/"))
    try:
        parsed.port
    except ValueError:
        raise ValueError("インスタンス URL のポート番号が不正です")
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("HTTPS のインスタンス URL を指定してください")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("インスタンス URL にパスやクエリは指定できません")
    if parsed.scheme == "http":
        host = parsed.hostname.casefold()
        try:
            address = ipaddress.ip_address(host)
            is_private = address.is_private or address.is_loopback or address.is_link_local
        except ValueError:
            is_private = host == "localhost" or host.endswith(".localhost")
        if not is_private:
            raise ValueError("HTTP はローカルまたはプライベートネットワーク内のインスタンスに限ります")
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_external_error(status_code: int | None = None) -> str:
    if status_code in (401, 403):
        return "認証に失敗しました。トークンと必要な権限を確認してください"
    if status_code == 429:
        return "サービス側で利用が制限されています。時間をおいて再試行してください"
    if status_code is not None and status_code >= 500:
        return "SNS サービスでエラーが発生しました。時間をおいて再試行してください"
    return "SNS サービスに接続できません。URL とネットワークを確認してください"


def _call_json(method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
               json_body: dict | None = None, data: dict | None = None, files: dict | None = None,
               content: bytes | None = None, content_type: str | None = None,
               allow_ok_false: bool = False) -> dict | list:
    request_headers = dict(headers or {})
    if content_type:
        request_headers["Content-Type"] = content_type
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream(method, url, headers=request_headers, params=params, json=json_body,
                               data=data, files=files, content=content) as response:
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise SocialServiceError("SNS サービスから大きすぎる応答が返されました")
                if response.status_code < 200 or response.status_code >= 300:
                    raise SocialServiceError(_safe_external_error(response.status_code))
                if response.status_code in (204, 205) or not payload:
                    return {}
                result = json.loads(payload)
                if not isinstance(result, (dict, list)):
                    raise SocialServiceError("SNS サービスの応答形式が不正です")
                if isinstance(result, dict) and (result.get("error") or (result.get("ok") is False and not allow_ok_false)):
                    raise SocialServiceError("SNS サービスが要求を受け付けませんでした。設定と投稿内容を確認してください")
                return result
    except SocialServiceError:
        raise
    except (httpx.HTTPError, json.JSONDecodeError, OSError, ValueError):
        raise SocialServiceError(_safe_external_error())


def _public_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def public_settings(db: Session) -> dict:
    accounts = _accounts(db)
    public_accounts = []
    for account in accounts:
        provider = account.get("provider")
        if provider == "bluesky":
            public_accounts.append({
                "id": account["id"], "provider": provider, "configured": True,
                "label": str(account.get("handle") or account.get("identifier") or "Bluesky"),
                "service_url": str(account.get("service_url") or "https://bsky.social"),
            })
        elif provider == "misskey":
            host = account.get("host") or urlparse(account.get("instance_url", "")).hostname
            username = str(account.get("username") or "")
            label = f"@{username}@{host}" if username and host else str(account.get("instance_url") or "Misskey")
            public_accounts.append({
                "id": account["id"], "provider": provider, "configured": True,
                "label": label, "instance_url": str(account.get("instance_url") or ""),
            })
    bluesky_accounts = [item for item in public_accounts if item["provider"] == "bluesky"]
    misskey_accounts = [item for item in public_accounts if item["provider"] == "misskey"]
    bluesky = bluesky_accounts[0] if bluesky_accounts else None
    misskey = misskey_accounts[0] if misskey_accounts else None
    return {
        "accounts": public_accounts,
        "bluesky": {"configured": bool(bluesky), "identifier": "",
                    "service_url": bluesky.get("service_url", "https://bsky.social") if bluesky else "https://bsky.social"},
        "misskey": {"configured": bool(misskey), "instance_url": misskey.get("instance_url", "") if misskey else ""},
    }


def configure_bluesky(db: Session, service_url: str, identifier: str, app_password: str) -> dict:
    service_url = _validated_instance_url(service_url)
    session = _call_json("POST", f"{service_url}/xrpc/com.atproto.server.createSession",
                         json_body={"identifier": identifier.strip(), "password": app_password})
    if not isinstance(session, dict):
        raise SocialServiceError("Bluesky の応答形式が不正です")
    if not all(isinstance(session.get(key), str) and session[key] for key in ("did", "handle", "refreshJwt")):
        raise SocialServiceError("Bluesky の応答に必要なアカウント情報がありません")
    _store_account(db, {
        "provider": "bluesky",
        "service_url": service_url,
        "identifier": identifier.strip(),
        "did": session["did"],
        "handle": session["handle"],
        "refresh_token": _encrypt(session["refreshJwt"]),
    })
    return public_settings(db)


def _cleanup_miauth_flows(now: float) -> None:
    for flow_id, flow in list(_MIAUTH_FLOWS.items()):
        if flow.get("cleanup_at", flow["expires_at"] + MIAUTH_FLOW_RETENTION_SECONDS) <= now:
            del _MIAUTH_FLOWS[flow_id]


def start_misskey_auth(instance_url: str) -> dict:
    instance_url = _validated_instance_url(instance_url)
    now = time.monotonic()
    session_id = str(uuid.uuid4())
    flow_id = uuid.uuid4().hex
    query = urlencode({"name": "YouTube Live Archiver", "permission": ",".join(MIAUTH_PERMISSIONS)})
    authorization_url = f"{instance_url}/miauth/{session_id}?{query}"
    with _MIAUTH_LOCK:
        _cleanup_miauth_flows(now)
        active = sum(flow.get("state") == "pending" for flow in _MIAUTH_FLOWS.values())
        if active >= MAX_MIAUTH_FLOWS:
            raise SocialServiceError("Misskey の認証処理が多すぎます。少し待ってから再試行してください")
        _MIAUTH_FLOWS[flow_id] = {
            "instance_url": instance_url,
            "session_id": session_id,
            "state": "pending",
            "expires_at": now + MIAUTH_FLOW_TTL_SECONDS,
            "cleanup_at": now + MIAUTH_FLOW_TTL_SECONDS + MIAUTH_FLOW_RETENTION_SECONDS,
            "checking": False,
            "result": None,
        }
    return {
        "flow_id": flow_id,
        "authorization_url": authorization_url,
        "expires_in": MIAUTH_FLOW_TTL_SECONDS,
    }


def check_misskey_auth(db: Session, flow_id: str) -> dict:
    now = time.monotonic()
    with _MIAUTH_LOCK:
        _cleanup_miauth_flows(now)
        flow = _MIAUTH_FLOWS.get(flow_id)
        if flow is None:
            raise KeyError(flow_id)
        if flow["state"] != "pending":
            return flow["result"] or {"state": flow["state"]}
        if flow["expires_at"] <= now:
            flow["state"] = "expired"
            flow["cleanup_at"] = now + MIAUTH_FLOW_RETENTION_SECONDS
            return {"state": "expired"}
        if flow["checking"]:
            return {"state": "pending"}
        flow["checking"] = True
        instance_url = flow["instance_url"]
        session_id = flow["session_id"]

    try:
        response = _call_json(
            "POST", f"{instance_url}/api/miauth/{session_id}/check",
            json_body={}, allow_ok_false=True,
        )
    except Exception:
        with _MIAUTH_LOCK:
            current = _MIAUTH_FLOWS.get(flow_id)
            if current and current["state"] == "pending":
                current["checking"] = False
        raise

    if not isinstance(response, dict):
        with _MIAUTH_LOCK:
            current = _MIAUTH_FLOWS.get(flow_id)
            if current and current["state"] == "pending":
                current["checking"] = False
        raise SocialServiceError("Misskey の認証応答が不正です")
    if response.get("ok") is False:
        with _MIAUTH_LOCK:
            current = _MIAUTH_FLOWS.get(flow_id)
            if current and current["state"] == "pending":
                current["checking"] = False
        return {"state": "pending"}

    token = response.get("token")
    user = response.get("user")
    if not isinstance(token, str) or not token or not isinstance(user, dict) or not isinstance(user.get("username"), str) or not user["username"]:
        with _MIAUTH_LOCK:
            current = _MIAUTH_FLOWS.get(flow_id)
            if current and current["state"] == "pending":
                current["checking"] = False
        raise SocialServiceError("Misskey の認証応答に必要なアカウント情報がありません")

    with _MIAUTH_LOCK:
        current = _MIAUTH_FLOWS.get(flow_id)
        if current is None:
            raise KeyError(flow_id)
        if current["state"] != "pending":
            return current["result"] or {"state": current["state"]}
        if current["expires_at"] <= time.monotonic():
            current["state"] = "expired"
            current["cleanup_at"] = time.monotonic() + MIAUTH_FLOW_RETENTION_SECONDS
            current["checking"] = False
            return {"state": "expired"}
        try:
            _store_account(db, {
                "provider": "misskey",
                "instance_url": instance_url,
                "token": _encrypt(token),
                "username": user["username"],
                "host": user.get("host"),
            })
            result = {"state": "connected", "settings": public_settings(db)}
        except Exception:
            current["checking"] = False
            raise
        current["state"] = "connected"
        current["result"] = result
        current["cleanup_at"] = time.monotonic() + MIAUTH_FLOW_RETENTION_SECONDS
        current["checking"] = False
        return result


def cancel_misskey_auth(flow_id: str) -> bool:
    now = time.monotonic()
    with _MIAUTH_LOCK:
        _cleanup_miauth_flows(now)
        flow = _MIAUTH_FLOWS.get(flow_id)
        if flow is None:
            return False
        if flow["state"] == "pending":
            flow["state"] = "cancelled"
            flow["result"] = {"state": "cancelled"}
            flow["cleanup_at"] = now + MIAUTH_FLOW_RETENTION_SECONDS
            flow["checking"] = False
        return True


def disconnect(db: Session, provider: str) -> dict:
    _save_accounts(db, [account for account in _accounts(db) if account.get("provider") != provider])
    return public_settings(db)


def disconnect_account(db: Session, account_id: str) -> dict:
    accounts = _accounts(db)
    remaining = [account for account in accounts if account.get("id") != account_id]
    if len(remaining) == len(accounts):
        raise KeyError(account_id)
    _save_accounts(db, remaining)
    return public_settings(db)


def normalize_tag(tag: str) -> str:
    normalized = tag.strip().removeprefix("#")
    if not re.fullmatch(r"[\w]{1,64}", normalized, flags=re.UNICODE):
        raise ValueError("ハッシュタグは1〜64文字の英数字・アンダースコアで指定してください")
    return normalized


def _date_key(value: str | None) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def _search_bluesky(tag: str, cursor: str | None) -> tuple[list[dict], str | None]:
    params = {"tag": tag, "limit": "25"}
    if cursor:
        params["cursor"] = cursor
    response = _call_json("GET", "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts", params=params)
    items = []
    posts = response.get("posts", []) if isinstance(response, dict) else []
    for row in posts if isinstance(posts, list) else []:
        if not isinstance(row, dict):
            continue
        post = row.get("post") if isinstance(row.get("post"), dict) else row
        if not isinstance(post, dict):
            continue
        record = post.get("record") or {}
        author = post.get("author") or {}
        if not isinstance(record, dict):
            record = {}
        if not isinstance(author, dict):
            author = {}
        uri = str(post.get("uri") or "")
        rkey = uri.rsplit("/", 1)[-1]
        handle = str(author.get("handle") or "")
        images = []
        embed = post.get("embed") if isinstance(post.get("embed"), dict) else {}
        for image in embed.get("images", []) if isinstance(embed.get("images"), list) else []:
            if not isinstance(image, dict):
                continue
            image_url = _public_url(image.get("fullsize") or image.get("thumb"))
            if image_url:
                images.append({"url": image_url, "alt": str(image.get("alt") or "")[:300]})
        items.append({"provider": "bluesky", "id": uri, "author": str(author.get("displayName") or handle),
                      "handle": handle, "text": str(record.get("text") or ""),
                      "created_at": _date_key(record.get("createdAt") or post.get("indexedAt")),
                      "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None,
                      "images": images})
    return items, response.get("cursor") if isinstance(response, dict) else None


def _search_misskey(config: dict, tag: str, cursor: str | None) -> tuple[list[dict], str | None]:
    instance_url = config["instance_url"]
    body: dict = {"i": _decrypt(config["token"]), "tag": tag, "limit": 25}
    if cursor:
        body["untilId"] = cursor
    response = _call_json("POST", f"{instance_url}/api/notes/search-by-tag", json_body=body)
    notes = response if isinstance(response, list) else (
        response.get("notes", []) if isinstance(response, dict) and isinstance(response.get("notes"), list) else []
    )
    items = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        user = note.get("user") or {}
        if not isinstance(user, dict):
            user = {}
        host = user.get("host") or config.get("host") or urlparse(instance_url).hostname
        username = str(user.get("username") or "")
        files = []
        for file in note.get("files", []) if isinstance(note.get("files"), list) else []:
            if not isinstance(file, dict):
                continue
            url = _public_url(file.get("thumbnailUrl") or file.get("url"))
            if url:
                files.append({"url": url, "alt": str(file.get("comment") or "")[:300]})
        note_id = str(note.get("id") or "")
        profile = f"@{username}@{host}" if username and host else username
        items.append({"provider": "misskey", "id": note_id, "author": str(user.get("name") or profile),
                      "handle": profile, "text": str(note.get("text") or ""),
                      "created_at": _date_key(note.get("createdAt")),
                      "url": f"{instance_url}/notes/{note_id}" if note_id else None, "images": files})
    next_cursor = str(notes[-1].get("id")) if len(notes) == 25 and isinstance(notes[-1], dict) else None
    return items, next_cursor


def search_hashtags(db: Session, tag: str, providers: list[str], bluesky_cursor: str | None = None,
                    misskey_cursor: str | None = None, misskey_account_id: str | None = None) -> dict:
    tag = normalize_tag(tag)
    items: list[dict] = []
    state = {}
    misskey_accounts = [account for account in _accounts(db) if account.get("provider") == "misskey"]
    config = next(
        (account for account in misskey_accounts if account.get("id") == misskey_account_id),
        misskey_accounts[0] if misskey_accounts and not misskey_account_id else None,
    )
    for provider in providers:
        try:
            if provider == "bluesky":
                rows, cursor = _search_bluesky(tag, bluesky_cursor)
            elif provider == "misskey":
                if misskey_account_id and not config:
                    state[provider] = {
                        "configured": bool(misskey_accounts),
                        "error": "検索する Misskey アカウントを設定から選び直してください",
                        "cursor": None,
                    }
                    continue
                if not config:
                    state[provider] = {"configured": False, "error": "設定画面で Misskey を接続してください", "cursor": None}
                    continue
                rows, cursor = _search_misskey(config, tag, misskey_cursor)
            else:
                continue
            state[provider] = {
                "configured": True, "error": None, "cursor": cursor,
                "account_id": config.get("id") if provider == "misskey" and config else None,
            }
            items.extend(rows)
        except SocialServiceError as exc:
            state[provider] = {
                "configured": provider != "misskey" or bool(config),
                "error": str(exc), "cursor": None,
                "account_id": config.get("id") if provider == "misskey" and config else None,
            }
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"tag": tag, "items": items, "providers": state}


def _jpeg_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        return None
    offset = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(payload):
            break
        length = int.from_bytes(payload[offset:offset + 2], "big")
        if length < 2 or offset + length > len(payload):
            break
        if marker in sof_markers and length >= 7:
            height = int.from_bytes(payload[offset + 3:offset + 5], "big")
            width = int.from_bytes(payload[offset + 5:offset + 7], "big")
            return width, height
        offset += length
    return None


def _decode_image(image: dict) -> bytes:
    if image.get("mime_type") != "image/jpeg":
        raise ValueError("キャプチャ画像は JPEG 形式にしてください")
    try:
        payload = base64.b64decode(image["data_base64"], validate=True)
    except (binascii.Error, ValueError, KeyError):
        raise ValueError("キャプチャ画像のデータが不正です")
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("画像は1枚あたり2 MB以下にしてください")
    dimensions = _jpeg_dimensions(payload)
    if not dimensions or dimensions != (image.get("width"), image.get("height")):
        raise ValueError("画像サイズを確認できません。キャプチャを取り直してください")
    if dimensions[0] * dimensions[1] > 16_000_000:
        raise ValueError("画像の解像度が大きすぎます")
    try:
        with Image.open(io.BytesIO(payload)) as image_file:
            if image_file.format != "JPEG" or image_file.size != dimensions:
                raise ValueError("キャプチャ画像の形式またはサイズが不正です")
            image_file.verify()
    except ValueError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("キャプチャ画像を読み取れません。取り直してください")
    return payload


def _post_text(body: dict, youtube_video_id: str) -> tuple[str, list[tuple[str, str, str]]]:
    text = str(body.get("text") or "").strip()
    tags = [normalize_tag(tag) for tag in body.get("hashtags", [])]
    existing_tags = {
        match.group(1).casefold()
        for match in re.finditer(r"(?<!\w)#([\w]{1,64})(?!\w)", text, flags=re.UNICODE)
    }
    for tag in tags:
        if tag.casefold() not in existing_tags:
            text += ("\n" if text else "") + f"#{tag}"
            existing_tags.add(tag.casefold())
    if body.get("include_video_link"):
        text += ("\n" if text else "") + f"https://www.youtube.com/watch?v={youtube_video_id}"
    if len(text) > 300:
        raise ValueError("投稿本文とハッシュタグは300文字以内にしてください")
    facets = []
    for match in re.finditer(r"(?<!\w)#([\w]{1,64})(?!\w)", text, flags=re.UNICODE):
        start = len(text[:match.start()].encode("utf-8"))
        end = len(text[:match.end()].encode("utf-8"))
        facets.append((str(start), str(end), match.group(1)))
    return text, facets


def _post_bluesky(db: Session, config: dict, text: str, facets: list[tuple[str, str, str]], images: list[dict]) -> dict:
    service_url = config["service_url"]
    refresh_token = _decrypt(config["refresh_token"])
    session = _call_json("POST", f"{service_url}/xrpc/com.atproto.server.refreshSession",
                         headers={"Authorization": f"Bearer {refresh_token}"})
    if not isinstance(session, dict):
        raise SocialServiceError("Bluesky の応答形式が不正です")
    access_token = session.get("accessJwt")
    if not access_token:
        raise SocialServiceError("Bluesky の認証を更新できません。設定を再登録してください")
    if isinstance(session.get("refreshJwt"), str):
        config["refresh_token"] = _encrypt(session["refreshJwt"])
        _save_account(db, config)
    headers = {"Authorization": f"Bearer {access_token}"}
    image_embeds = []
    for image in images:
        blob = _call_json("POST", f"{service_url}/xrpc/com.atproto.repo.uploadBlob", headers=headers,
                          content=image["bytes"], content_type="image/jpeg").get("blob")
        if not isinstance(blob, dict):
            raise SocialServiceError("Bluesky に画像をアップロードできませんでした")
        image_embeds.append({"alt": image["alt"], "image": blob})
    record: dict = {"$type": "app.bsky.feed.post", "text": text,
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    if facets:
        record["facets"] = [{"index": {"byteStart": int(start), "byteEnd": int(end)},
                             "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]}
                            for start, end, tag in facets]
    if image_embeds:
        record["embed"] = {"$type": "app.bsky.embed.images", "images": image_embeds}
    result = _call_json("POST", f"{service_url}/xrpc/com.atproto.repo.createRecord", headers=headers,
                        json_body={"repo": config["did"], "collection": "app.bsky.feed.post", "record": record})
    uri = str(result.get("uri") or "")
    rkey = uri.rsplit("/", 1)[-1]
    return {"success": True, "url": f"https://bsky.app/profile/{config['handle']}/post/{rkey}" if rkey else None}


def _post_misskey(config: dict, text: str, images: list[dict], visibility: str) -> dict:
    instance_url = config["instance_url"]
    token = _decrypt(config["token"])
    file_ids = []
    for image in images:
        response = _call_json("POST", f"{instance_url}/api/drive/files/create",
                              data={"i": token, "comment": image["alt"]},
                              files={"file": ("capture.jpg", image["bytes"], "image/jpeg")})
        if not isinstance(response, dict):
            raise SocialServiceError("Misskey の応答形式が不正です")
        file_data = response.get("file")
        file = response.get("id") or (file_data.get("id") if isinstance(file_data, dict) else None)
        if not isinstance(file, str):
            raise SocialServiceError("Misskey に画像をアップロードできませんでした")
        file_ids.append(file)
    note = _call_json("POST", f"{instance_url}/api/notes/create",
                       json_body={"i": token, "text": text or None, "fileIds": file_ids or None,
                                  "visibility": visibility})
    if not isinstance(note, dict):
        raise SocialServiceError("Misskey の応答形式が不正です")
    created_note = note.get("createdNote")
    note_id = created_note.get("id") if isinstance(created_note, dict) else None
    return {"success": True, "url": f"{instance_url}/notes/{note_id}" if note_id else None}


def post_to_social(db: Session, video, body) -> dict:
    text, facets = _post_text(body.model_dump(), video.youtube_video_id)
    images = []
    for image in body.images:
        item = image.model_dump()
        item["bytes"] = _decode_image(item)
        images.append(item)
    if not text and not images:
        raise ValueError("本文、ハッシュタグ、またはキャプチャ画像を追加してください")
    if body.visibility not in ("public", "home", "followers"):
        raise ValueError("Misskey の公開範囲は公開・ホーム・フォロワー限定から選んでください")
    configs = {account.get("id"): account for account in _accounts(db)}
    if any(target not in configs for target in body.targets):
        raise ValueError("投稿先アカウントが設定されていません。設定を再読み込みしてください")
    public_accounts = {account["id"]: account for account in public_settings(db)["accounts"]}
    results = {}
    for account_id in body.targets:
        config = configs[account_id]
        provider = config.get("provider")
        public_account = public_accounts.get(
            account_id, {"label": provider.title() if isinstance(provider, str) else "SNS"},
        )
        try:
            if provider == "bluesky":
                result = _post_bluesky(db, config, text, facets, images)
            elif provider == "misskey":
                result = _post_misskey(config, text, images, body.visibility)
            else:
                raise SocialServiceError("SNS の種類を確認できません")
            results[account_id] = {"provider": provider, "label": public_account["label"], **result}
        except SocialServiceError as exc:
            results[account_id] = {
                "provider": provider, "label": public_account["label"],
                "success": False, "error": str(exc),
            }
        except Exception:
            results[account_id] = {
                "provider": provider, "label": public_account["label"],
                "success": False, "error": "投稿処理に失敗しました。設定と投稿内容を確認してください",
            }
    return {"results": results}
