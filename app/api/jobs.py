"""Job endpoints — create (upload), list, get, delete, download."""
from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, Page
from app.services.job_engine import (
    _delete_job_files,
    _job_dir,
    _out_dir,
    _natural_key,
    clear_all_jobs,
    ingest_upload,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dict(job: Job, with_pages: bool = False) -> dict:
    d = {
        "id": job.id,
        "name": job.name,
        "output_mode": job.output_mode,
        "model_id": job.model_id,
        "status": job.status,
        "stage": job.stage,
        "pages_total": job.pages_total,
        "pages_done": job.pages_done,
        "blocks_found": job.blocks_found,
        "blocks_ok": job.blocks_ok,
        "tokens_used": job.tokens_used,
        "cost_usd": round(job.cost_usd or 0.0, 6),
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }
    if with_pages:
        d["pages"] = [
            {"index": p.index, "status": p.status, "error": p.error, "blocks": len(p.blocks)}
            for p in job.pages
        ]
    return d


@router.post("")
def create_job(
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    output_mode: str = Form("folder"),
    model_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    if output_mode not in ("folder", "cbz", "mirror"):
        output_mode = "folder"
    # status="uploading" so the worker can't claim a half-ingested job (the
    # archive is streamed below before the pages are attached).
    job = Job(source="upload", name=name, output_mode=output_mode, model_id=model_id, status="uploading")
    db.add(job)
    db.commit()
    db.refresh(job)

    paths, source_format = ingest_upload(job.id, files)
    if not paths:
        db.delete(job)
        db.commit()
        raise HTTPException(400, "no image pages found in upload")
    job.source_format = source_format
    for i, p in enumerate(paths):
        db.add(Page(job_id=job.id, index=i, original_path=p))
    job.name = job.name or os.path.splitext(os.path.basename(paths[0]))[0]
    job.pages_total = len(paths)
    job.status = "queued"  # fully ingested — now claimable
    db.commit()
    db.refresh(job)
    return _job_dict(job, with_pages=True)


@router.get("")
def list_jobs(db: Session = Depends(get_db)):
    return [_job_dict(j) for j in db.query(Job).order_by(Job.id.desc()).all()]


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _job_dict(job, with_pages=True)


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    _delete_job_files(job_id)
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.delete("")
def clear_all(db: Session = Depends(get_db)):
    """Delete every job — DB rows and on-disk files."""
    n = clear_all_jobs(db)
    return {"ok": True, "deleted": n}


@router.post("/{job_id}/start")
def start_job(job_id: int, db: Session = Depends(get_db)):
    """Resume a paused job, or retry a cancelled/failed one."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in ("paused", "cancelled", "failed"):
        job.status = "queued"
        job.error = None
        job.finished_at = None
        job.updated_at = _now()
        db.commit()
    return _job_dict(job)


@router.post("/{job_id}/pause")
def pause_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status == "running":
        job.status = "paused"
        job.updated_at = _now()
        db.commit()
    return _job_dict(job)


@router.post("/{job_id}/stop")
def stop_job(job_id: int, db: Session = Depends(get_db)):
    """Cancel a queued/running/paused job (worker stops between pages)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in ("queued", "running", "paused"):
        job.status = "cancelled"
        job.updated_at = _now()
        db.commit()
    return _job_dict(job)


@router.get("/{job_id}/download")
def download(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # Prefer an assembled CBZ, then an assembled ZIP, else zip the folder on the fly.
    for ext in ("cbz", "zip"):
        arc = os.path.join(_job_dir(job_id), f"translated.{ext}")
        if os.path.exists(arc):
            return FileResponse(arc, filename=f"{job.name}.{ext}")
    out_dir = _out_dir(job_id)
    pages = sorted(
        [f for f in os.listdir(out_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))],
        key=_natural_key,
    )
    if not pages:
        raise HTTPException(404, "no output pages yet")
    zip_path = os.path.join(_job_dir(job_id), "translated.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for name in pages:
            zf.write(os.path.join(out_dir, name), arcname=name)
    return FileResponse(zip_path, filename=f"{job.name}.zip")
