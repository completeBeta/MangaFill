"""Job endpoints — create (upload), list, get, delete, download."""
from __future__ import annotations

import os
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, Page, TextBlock
from app.services import audit
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
    name = job.name
    db.delete(job)
    db.commit()
    audit.record("jobs.delete", target=f"job {job_id}", detail={"name": name})
    return {"ok": True}


@router.delete("")
def clear_all(db: Session = Depends(get_db)):
    """Delete every job — DB rows and on-disk files."""
    n = clear_all_jobs(db)
    # Destructive and irreversible: say so explicitly, with the count. Without this
    # the action was invisible in the log (the complaint that started this).
    audit.record("jobs.clear_all", target="all jobs",
                 detail={"jobs_deleted": n, "irreversible": True})
    return {"ok": True, "deleted": n}


@router.post("/{job_id}/start")
def start_job(job_id: int, db: Session = Depends(get_db)):
    """Resume a job — re-queue it and retry every page that isn't finished.

    Accepts paused/cancelled/failed/partial **and** done: a job that finished
    "partial" (some pages failed, e.g. a provider outage) or a job whose pages
    you want retried after a pipeline fix can all be resumed. The engine skips
    pages already marked `done`, so only unfinished work is redone — a Resume
    never re-renders finished pages. Pages left `running` by a crash are reset
    first so the worker re-claims them.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in ("paused", "cancelled", "failed", "partial", "done"):
        for p in db.query(Page).filter(
            Page.job_id == job_id, Page.status.in_(("running", "failed", "pending"))
        ).all():
            p.status = "pending"
            p.error = None
        job.status = "queued"
        job.error = None
        job.finished_at = None
        job.updated_at = _now()
        db.commit()
        audit.record("jobs.resume", target=f"job {job_id}",
                     detail={"name": job.name, "status": job.status})
    return _job_dict(job, with_pages=True)


@router.post("/{job_id}/rerender")
def rerender_pages(job_id: int, payload: dict | None = Body(None), db: Session = Depends(get_db)):
    """Re-render pages of a job with the current pipeline — all of them, or a set.

    Body (optional): ``{"indices": [3, 7]}`` to redo specific pages; omit it (or
    send an empty list) to redo EVERY page. Use after a pipeline fix to apply it
    to pages produced by older code, since a normal Resume skips pages already
    marked `done`.

    Resetting several pages in ONE request matters: a per-page call queues the job
    and the worker claims it within a second, so a burst of per-page calls races
    the runner (the later ones get 409 "job is running"). This endpoint resets the
    whole set before the job is queued, so one run re-renders all of them.

    Token/cost totals are left alone (they record real spend); `pages_done` /
    `blocks_found` / `blocks_ok` have the redone pages' contribution subtracted
    first, because the engine adds them again as each page completes.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status == "running":
        raise HTTPException(409, "job is running — pause or stop it first")

    indices = (payload or {}).get("indices") or None
    q = db.query(Page).filter(Page.job_id == job_id)
    if indices:
        try:
            wanted = [int(i) for i in indices]
        except (TypeError, ValueError):
            raise HTTPException(400, "indices must be a list of page numbers")
        pages = q.filter(Page.index.in_(wanted)).all()
        if len(pages) != len(set(wanted)):
            raise HTTPException(404, "one or more pages not found")
    else:
        pages = q.all()

    for p in pages:
        if p.status == "done":
            if job.pages_done > 0:
                job.pages_done -= 1
            old = db.query(TextBlock).filter(TextBlock.page_id == p.id).all()
            job.blocks_found = max(0, job.blocks_found - len(old))
            job.blocks_ok = max(0, job.blocks_ok - sum(1 for b in old if (b.en_text or "").strip()))
        p.status = "pending"
        p.error = None

    job.status = "queued"
    job.error = None
    job.finished_at = None
    job.updated_at = _now()
    db.commit()
    audit.record("jobs.rerender", target=f"job {job_id}",
                 detail={"pages": len(pages)})
    return {"ok": True, "pages_queued": len(pages),
            "indices": sorted(p.index for p in pages)}


@router.post("/{job_id}/pages/{index}/rerender")
def rerender_page(job_id: int, index: int, db: Session = Depends(get_db)):
    """Re-render a SINGLE page with the current pipeline, leaving the rest alone.

    Resets just this page and re-queues the job; the engine skips every page still
    marked `done`, so only this one is rendered again. Use it after a pipeline fix
    to apply that fix to pages produced by the older code — far cheaper than
    re-running the whole job (and it re-spends tokens for this page only).

    Counters are kept honest: a page that had already contributed to
    `pages_done` / `blocks_found` / `blocks_ok` is subtracted first, because the
    engine adds them again when the page is redone.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    p = db.query(Page).filter(Page.job_id == job_id, Page.index == index).first()
    if p is None:
        raise HTTPException(404, "page not found")
    if job.status == "running":
        raise HTTPException(409, "job is running — pause or stop it first")

    if p.status == "done":
        if job.pages_done > 0:
            job.pages_done -= 1
        old = db.query(TextBlock).filter(TextBlock.page_id == p.id).all()
        job.blocks_found = max(0, job.blocks_found - len(old))
        job.blocks_ok = max(0, job.blocks_ok - sum(1 for b in old if (b.en_text or "").strip()))

    p.status = "pending"
    p.error = None
    job.status = "queued"
    job.error = None
    job.finished_at = None
    job.updated_at = _now()
    db.commit()
    return {"ok": True, "job": _job_dict(job), "page": index}


@router.post("/{job_id}/pause")
def pause_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status == "running":
        job.status = "paused"
        job.updated_at = _now()
        db.commit()
        audit.record("jobs.pause", target=f"job {job_id}", detail={"name": job.name})
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
        audit.record("jobs.stop", target=f"job {job_id}", detail={"name": job.name})
    return _job_dict(job)


def _download_ext(output_mode: str, source_format: str) -> str:
    """Archive extension the download endpoint should serve (.cbz / .zip).

    Respect the output mode, then round-trip the upload format so a .cbz upload
    comes back as .cbz (comic readers won't open a file named .zip). Folder
    uploads default to .cbz.
    """
    if output_mode == "cbz":
        return "cbz"
    if output_mode == "mirror" and source_format in ("cbz", "zip"):
        return source_format
    return source_format if source_format in ("cbz", "zip") else "cbz"


def _out_pages(out_dir: str) -> list[str]:
    """Output page filenames in reading order."""
    return sorted(
        [f for f in os.listdir(out_dir)
         if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))],
        key=_natural_key,
    )


def _fingerprint(out_dir: str, pages: list[str]) -> dict:
    """Name -> [mtime_ns, size] for every output page (the archive's cache key)."""
    fp: dict = {}
    for name in pages:
        try:
            st = os.stat(os.path.join(out_dir, name))
        except OSError:
            continue
        fp[name] = [int(st.st_mtime_ns), st.st_size]
    return fp


def _read_manifest(path: str) -> dict | None:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _download_archive(job_id: int, ext: str) -> str:
    """Path to the job's archive, REBUILT whenever the rendered pages changed.

    v0.27.16. The archive used to be built once and reused forever
    (`if not os.path.exists(arc)`), so any page re-rendered after the first
    download never made it into the file the user received — the download kept
    serving the ORIGINAL render, which is exactly what was reported. It is now a
    cache keyed on a manifest of the output pages (name, mtime_ns, size), so a
    re-render, a resumed page, or a deleted page invalidates it.
    """
    out_dir = _out_dir(job_id)
    pages = _out_pages(out_dir) if os.path.isdir(out_dir) else []
    if not pages:
        raise HTTPException(404, "no output pages yet")
    arc = os.path.join(_job_dir(job_id), f"translated.{ext}")
    manifest = arc + ".manifest.json"
    fp = _fingerprint(out_dir, pages)
    if os.path.exists(arc) and _read_manifest(manifest) == fp:
        return arc
    tmp = f"{arc}.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for name in pages:
            zf.write(os.path.join(out_dir, name), arcname=name)
    os.replace(tmp, arc)  # atomic: a concurrent download never sees a partial zip
    with open(manifest, "w") as fh:
        json.dump(fp, fh)
    return arc


@router.get("/{job_id}/download")
def download(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    ext = _download_ext(job.output_mode, job.source_format)
    arc = _download_archive(job_id, ext)
    resp = FileResponse(arc, filename=f"{job.name}.{ext}")
    # Rebuilt in place when pages change: a browser-cached copy would be the
    # stale render the manifest exists to avoid.
    resp.headers["Cache-Control"] = "no-store"
    return resp
