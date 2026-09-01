"""Settings endpoints — read/write the persisted runtime settings.

Model / base URL / API keys / dry-run / output mode are editable here; the
worker reads the same store, so changes take effect on the next job.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.settings_store import get_all, set_setting

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
    return get_all(db)
