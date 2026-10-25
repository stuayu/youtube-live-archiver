from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .common import router
from ..db import get_db
from ..models import Video
from ..schemas import BlueskySettingsIn, MisskeyAuthStartIn, SocialPostIn
from ..services import social as social_service


@router.get("/settings/social")
def get_social_settings(db: Session = Depends(get_db)):
    return social_service.public_settings(db)


@router.put("/settings/social/bluesky")
def put_bluesky_settings(data: BlueskySettingsIn, db: Session = Depends(get_db)):
    try:
        return social_service.configure_bluesky(db, data.service_url, data.identifier, data.app_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except social_service.SocialServiceError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/settings/social/bluesky")
def delete_bluesky_settings(db: Session = Depends(get_db)):
    return social_service.disconnect(db, "bluesky")


@router.post("/settings/social/misskey/miauth")
def start_misskey_auth(data: MisskeyAuthStartIn):
    try:
        return social_service.start_misskey_auth(data.instance_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except social_service.SocialServiceError as exc:
        raise HTTPException(400, str(exc))


@router.post("/settings/social/misskey/miauth/{flow_id}/check")
def check_misskey_auth(flow_id: str, db: Session = Depends(get_db)):
    try:
        return social_service.check_misskey_auth(db, flow_id)
    except KeyError:
        raise HTTPException(404, "Misskey の認証処理が見つかりません。最初からやり直してください")
    except social_service.SocialServiceError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/settings/social/misskey/miauth/{flow_id}")
def cancel_misskey_auth(flow_id: str):
    return {"cancelled": social_service.cancel_misskey_auth(flow_id)}


@router.delete("/settings/social/misskey")
def delete_misskey_settings(db: Session = Depends(get_db)):
    return social_service.disconnect(db, "misskey")


@router.delete("/settings/social/accounts/{account_id}")
def delete_social_account(account_id: str, db: Session = Depends(get_db)):
    try:
        return social_service.disconnect_account(db, account_id)
    except KeyError:
        raise HTTPException(404, "SNS アカウントがありません")


@router.get("/social/search")
def search_social_hashtags(
    tag: str = Query(min_length=1, max_length=65),
    providers: str = Query(default="bluesky,misskey", max_length=30),
    bluesky_cursor: str | None = Query(default=None, max_length=1000),
    misskey_cursor: str | None = Query(default=None, max_length=200),
    misskey_account_id: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
):
    selected = [value.strip() for value in providers.split(",") if value.strip()]
    if not selected or len(selected) != len(set(selected)) or any(value not in ("bluesky", "misskey") for value in selected):
        raise HTTPException(422, "検索先が不正です")
    try:
        return social_service.search_hashtags(
            db, tag, selected, bluesky_cursor, misskey_cursor, misskey_account_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/videos/{video_id}/social/post")
def post_social(video_id: int, data: SocialPostIn, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "動画がありません")
    try:
        return social_service.post_to_social(db, video, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
