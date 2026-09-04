"""Settings endpoints — output mode + dry-run (model config lives under /api/models)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.pipeline.device import set_device
from app.settings_store import get_all, get_setting, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return get_all(db)


@router.put("")
def put_settings(payload: dict, db: Session = Depends(get_db)):
    changed = 0
    for key, value in payload.items():
        if set_setting(db, key, value):
            changed += 1
    if changed:
        db.commit()
    # A device change must take effect immediately (re-resolve local CPU/GPU).
    if "device" in payload:
        set_device(get_setting(db, "device"))
    return get_all(db)
