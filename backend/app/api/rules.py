from .common import *

@router.get("/recording-rules", response_model=list[RecordingRuleOut])
def recording_rules(db: Session = Depends(get_db)):
    result = db.scalars(select(RecordingRule).order_by(RecordingRule.priority, RecordingRule.id)).all()
    db.commit()
    return result

@router.post("/recording-rules", response_model=RecordingRuleOut)
def create_recording_rule(body: RecordingRuleIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    if data["channel_id"] is not None and db.get(Channel, data["channel_id"]) is None:
        raise HTTPException(422, "対象チャンネルがありません")
    if data["storage_id"] is not None and db.get(Storage, data["storage_id"]) is None:
        raise HTTPException(422, "保存先がありません")
    rule = RecordingRule(**data)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

@router.put("/recording-rules/{rule_id}", response_model=RecordingRuleOut)
def update_recording_rule(rule_id: int, body: RecordingRuleIn, db: Session = Depends(get_db)):
    rule = db.get(RecordingRule, rule_id)
    if rule is None: raise HTTPException(404, "ルールがありません")
    data = body.model_dump()
    if data["channel_id"] is not None and db.get(Channel, data["channel_id"]) is None:
        raise HTTPException(422, "対象チャンネルがありません")
    if data["storage_id"] is not None and db.get(Storage, data["storage_id"]) is None:
        raise HTTPException(422, "保存先がありません")
    for key, value in data.items(): setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule

@router.delete("/recording-rules/{rule_id}")
def delete_recording_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(RecordingRule, rule_id)
    if rule is None: raise HTTPException(404, "ルールがありません")
    db.delete(rule)
    db.commit()
    return {"ok": True}

@router.post("/recording-rules/preview")
def preview_recording_rule(body: RecordingRuleIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    videos = db.scalars(select(Video).order_by(Video.created_at.desc())).all()
    hits = []
    probe = type("RuleProbe", (), data)()
    for video in videos:
        if data["channel_id"] is not None and video.channel_id != data["channel_id"]: continue
        description = str((video.raw_metadata or {}).get("description") or "")
        if matches(probe, video.title or "", description):
            hits.append({"id": str(video.youtube_video_id), "title": video.title, "status": video.status})
    db.commit()
    return {"count": len(hits), "videos": hits[:20]}
