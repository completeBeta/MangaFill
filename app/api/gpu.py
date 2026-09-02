"""GPU status endpoint — the vision GPU (detect/OCR/inpaint) lives on a separate
host as a remote worker, wired later. This endpoint reports the current device
plus whether a remote worker URL is configured and reachable.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.settings_store import get_setting

router = APIRouter(prefix="/api/gpu", tags=["gpu"])


@router.get("")
def gpu_status(db: Session = Depends(get_db)):
    url = get_setting(db, "gpu_worker_url").strip()
    device = settings.device or "cpu"
    status = "not_configured"
    if url:
        try:
            r = httpx.get(f"{url.rstrip('/')}/health", timeout=2.0)
            status = "connected" if r.status_code == 200 else "unreachable"
        except Exception:
            status = "unreachable"
    return {"device": device, "worker_url": url, "status": status}
