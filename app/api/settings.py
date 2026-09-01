"""Settings endpoints — read/write the persisted output-mode (and surface config)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.db import get_db
from app.models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PERSISTED = {"output_mode"}


def _read(db: Session, key: str, default: str) -> str:
    s = db.get(Setting, key)
    return s.value if s else default


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return {
        "output_mode": _read(db, "output_mode", "folder"),
        "model": cfg.deepseek_model,
        "base_url": cfg.deepseek_base_url,
        "dry_run": cfg.dry_run,
    }


@router.put("")
def put_settings(payload: dict, db: Session = Depends(get_db)):
    for key, value in payload.items():
        if key not in _PERSISTED:
            continue
        if key == "output_mode" and value not in ("folder", "cbz"):
            continue
        s = db.get(Setting, key)
        if s is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            s.value = str(value)
    db.commit()
    return get_settings(db)
