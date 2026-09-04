"""GPU status endpoint — reports the local vision-model device (CPU / local GPU)
plus whether a remote GPU worker URL is configured and reachable.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.pipeline.device import backend, get_device, local_cuda_available
from app.settings_store import get_setting

router = APIRouter(prefix="/api/gpu", tags=["gpu"])


@router.get("")
def gpu_status(db: Session = Depends(get_db)):
    url = get_setting(db, "gpu_worker_url").strip()
    status = "not_configured"
    if url:
        try:
            r = httpx.get(f"{url.rstrip('/')}/health", timeout=2.0)
            status = "connected" if r.status_code == 200 else "unreachable"
        except Exception:
            status = "unreachable"
    return {
        "device": get_setting(db, "device"),     # configured (auto | cpu | cuda)
        "effective_device": get_device(),        # resolved local device (cpu | cuda)
        "cuda_available": local_cuda_available(),
        "backend": backend(),                    # cuda | rocm | cpu
        "worker_url": url,
        "status": status,
    }
