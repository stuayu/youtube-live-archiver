from .common import *

@router.get("/storages", response_model=list[StorageOut])
def storages(db: Session = Depends(get_db)):
    default_root = Path(settings.storage_root).resolve()
    result = [
        {
            **StorageOut.model_validate(item).model_dump(),
            "is_default": Path(item.root_path).resolve() == default_root,
        }
        for item in db.scalars(select(Storage).order_by(Storage.name)).all()
    ]
    db.commit()
    return result

@router.post("/storages", response_model=StorageOut)
def add_storage(body: StorageIn, db: Session = Depends(get_db)):
    import tempfile
    item = Storage(**body.model_dump())
    db.add(item)
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError): raise HTTPException(409, "保存先名が重複しています")
        raise
    root = Path(item.root_path)
    reachable = writable = False
    windows_root = item.root_path.startswith("\\\\") or bool(__import__("re").match(r"^[A-Za-z]:[\\/]", item.root_path))
    if not (windows_root and os.name != "nt"):
      try:
        root.mkdir(parents=True, exist_ok=True)
        reachable = root.is_dir()
        if reachable:
            with tempfile.NamedTemporaryFile(prefix=".yla-write-test-", dir=root, delete=True):
                pass
            writable = True
      except OSError:
        pass
    db.commit()
    db.refresh(item)
    return {**StorageOut.model_validate(item).model_dump(), "reachable": reachable, "writable": writable}

@router.get("/storages/usage")
def storage_usage(db: Session = Depends(get_db)):
    """Return filesystem availability and DB-tracked archive sizes."""
    import shutil
    result = []
    aggregates = select(Video.storage_id.label("storage_id"), func.coalesce(func.sum(Video.file_size), 0).label("app_bytes"),
                        func.count(Video.id).label("recording_count")).where(Video.file_path.is_not(None),
                        Video.status.not_in(("DELETED_BY_RETENTION", "DELETED_MANUALLY"))).group_by(Video.storage_id).subquery()
    rows = db.execute(select(Storage, aggregates.c.app_bytes, aggregates.c.recording_count)
            .outerjoin(aggregates, aggregates.c.storage_id == Storage.id).order_by(Storage.name)).all()
    db.commit()
    for storage, app_bytes, recording_count in rows:
        root = Path(storage.root_path)
        reachable = root.exists() and root.is_dir()
        writable = reachable and os.access(root, os.W_OK)
        try:
            usage = shutil.disk_usage(root if root.exists() else root.parent)
            total, free = usage.total, usage.free
        except OSError:
            total = free = None
        result.append({"id": str(storage.id), "name": storage.name, "root_path": storage.root_path,
            "total_bytes": total, "free_bytes": free,
            "used_bytes": total - free if total is not None and free is not None else None,
            "app_bytes": int(app_bytes or 0), "recording_count": int(recording_count or 0), "reachable": reachable, "writable": writable,
            "minimum_free_bytes": storage.minimum_free_bytes, "warning_free_bytes": storage.warning_free_bytes,
            "is_default": root.resolve() == Path(settings.storage_root).resolve(),
            "warning": free is not None and free < storage.warning_free_bytes,
            "critical": free is not None and free < storage.minimum_free_bytes})
    return result

@router.put("/storages/{storage_id}", response_model=StorageOut)
def update_storage(storage_id: int, body: StorageIn, db: Session = Depends(get_db)):
    item = db.get(Storage, storage_id)
    if not item: raise HTTPException(404, "ストレージがありません")
    for key, value in body.model_dump().items(): setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item

@router.patch("/storages/{storage_id}", response_model=StorageOut)
def patch_storage(storage_id: int, body: StoragePatch, db: Session = Depends(get_db)):
    item = db.get(Storage, storage_id)
    if not item: raise HTTPException(404, "ストレージがありません")
    values = body.model_dump(exclude_unset=True)
    for key, value in values.items(): setattr(item, key, value)
    if item.warning_free_bytes < item.minimum_free_bytes:
        raise HTTPException(422, "warning_free_bytes は minimum_free_bytes 以上にしてください")
    db.commit()
    db.refresh(item)
    return item

@router.delete("/storages/{storage_id}")
def delete_storage(storage_id: int, db: Session = Depends(get_db)):
    item = db.get(Storage, storage_id)
    if not item: raise HTTPException(404, "ストレージがありません")
    refs = {
        "channels": (db.scalar(select(func.count(Channel.id)).where(or_(Channel.storage_id == storage_id, Channel.fallback_storage_id == storage_id))) or 0),
        "videos": (db.scalar(select(func.count(Video.id)).where(Video.storage_id == storage_id)) or 0),
        "rules": (db.scalar(select(func.count(RecordingRule.id)).where(RecordingRule.storage_id == storage_id)) or 0),
    }
    if any(refs.values()): raise HTTPException(409, "参照中のため削除できません: " + ", ".join(f"{key}={value}" for key, value in refs.items() if value))
    db.delete(item)
    db.commit()
    return {"ok": True}
