"""Page image endpoints — serve original / translated pages for the side-by-side viewer."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Page

router = APIRouter(prefix="/api/jobs/{job_id}/pages", tags=["pages"])


def _get_page(db: Session, job_id: int, index: int) -> Page:
    p = db.query(Page).filter(Page.job_id == job_id, Page.index == index).first()
    if p is None:
        raise HTTPException(404, "page not found")
    return p


@router.get("/{index}/original")
def original(job_id: int, index: int, db: Session = Depends(get_db)):
    p = _get_page(db, job_id, index)
    if not p.original_path or not os.path.exists(p.original_path):
        raise HTTPException(404, "original not available")
    resp = FileResponse(p.original_path)
    # Re-runs rewrite these files in place, so a browser-cached copy would show
    # STALE pages (e.g. a caption that a later fix translated). Force a fresh
    # fetch every view — this is a QA viewer, not a high-traffic gallery.
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/{index}/translated")
def translated(job_id: int, index: int, db: Session = Depends(get_db)):
    p = _get_page(db, job_id, index)
    if not p.output_path or not os.path.exists(p.output_path):
        raise HTTPException(404, "translated not available yet")
    resp = FileResponse(p.output_path)
    resp.headers["Cache-Control"] = "no-store"
    return resp
