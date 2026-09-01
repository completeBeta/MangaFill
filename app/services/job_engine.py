"""Job engine — turn an uploaded job into translated pages.

Single worker: processes one page at a time, writes progress + per-page state to
SQLite as it goes (so the dashboard can show live progress). The headless pipeline
(`app.pipeline.render.render_translated_page`) does the actual detect → OCR →
translate → inpaint → typeset work; this module owns upload ingest, the page loop,
persistence, and output-mode assembly.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import datetime, timezone

from app.config import settings
from app.db import SessionLocal
from app.models import Job, Page, TextBlock
from app.pipeline.render import render_translated_page
from app.services.logging import get_logger
from app.settings_store import get_setting

log = get_logger("job_engine")

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _natural_key(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def resolve_translation(db) -> tuple[str, str, str, bool]:
    """Return (model, base_url, api_key, dry_run) from the persisted settings store.

    The selected base URL picks the key: OpenRouter -> openrouter key, anything
    else -> DeepSeek key. Falls back to the raw key file for local-dev when no
    key is configured (Docker gets keys via env / the persisted settings).
    """
    model = get_setting(db, "model")
    base_url = get_setting(db, "base_url")
    dry_run = get_setting(db, "dry_run") == "true"
    if "openrouter" in base_url.lower():
        key = get_setting(db, "openrouter_api_key")
    else:
        key = get_setting(db, "deepseek_api_key")
    if not key:
        # Local-dev fallback: read the raw key file directly.
        env = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env):
            for line in open(env):
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return model, base_url, key, dry_run


def _job_dir(job_id: int) -> str:
    return os.path.join(settings.jobs_dir, str(job_id))


def _orig_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "original")


def _out_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "output")


def ingest_upload(job_id: int, files: list) -> list[str]:
    """Save uploaded files (images and/or a .cbz) to the job's original dir, in
    natural reading order. Returns the sorted list of saved page paths."""
    orig = _orig_dir(job_id)
    os.makedirs(orig, exist_ok=True)
    paths: list[str] = []

    for f in files:
        name = f.filename or "page"
        low = name.lower()
        if low.endswith(".cbz"):
            zf = zipfile.ZipFile(io.BytesIO(f.file.read()))
            for member in sorted(zf.namelist(), key=_natural_key):
                if member.lower().endswith(_IMG_EXTS):
                    out = os.path.join(orig, os.path.basename(member))
                    with open(out, "wb") as o:
                        o.write(zf.read(member))
                    paths.append(out)
        elif low.endswith(_IMG_EXTS):
            out = os.path.join(orig, name)
            with open(out, "wb") as o:
                o.write(f.file.read())
            paths.append(out)

    paths.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return paths


def process_job(job_id: int) -> None:
    """Run the pipeline over every page of a job, persisting progress + blocks."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        job.updated_at = _now()
        db.commit()
        log.info("job %s: processing %d pages (mode=%s)", job_id, job.pages_total, job.output_mode)

        model, base_url, key, dry_run = resolve_translation(db)
        out_dir = _out_dir(job_id)
        os.makedirs(out_dir, exist_ok=True)
        log.info("job %s: model=%s dry_run=%s", job_id, model, dry_run)

        pages = db.query(Page).filter(Page.job_id == job_id).order_by(Page.index).all()
        for p in pages:
            p.status = "running"
            db.commit()
            try:
                img, blocks, cost = render_translated_page(p.original_path, model, key, base_url, dry_run=dry_run)
                out_path = os.path.join(out_dir, f"{p.index:04d}.png")
                img.save(out_path)
                p.output_path = out_path
                p.status = "done"
                p.error = None
                job.blocks_found += len(blocks)
                job.blocks_ok += sum(1 for b in blocks if b.translation)
                job.cost_usd += cost or 0.0
                # persist detected blocks (for the side-by-side viewer / logs)
                for b in blocks:
                    db.add(TextBlock(
                        page_id=p.id,
                        box=str(list(b.bbox)),
                        orientation=b.orientation,
                        jp_text=b.text,
                        en_text=b.translation or "",
                        confidence=b.confidence,
                    ))
                job.pages_done += 1
                log.info("job %s page %d/%d done (%d blocks, %d translated)",
                         job_id, job.pages_done, job.pages_total, len(blocks),
                         sum(1 for b in blocks if b.translation))
            except Exception as e:
                p.status = "failed"
                p.error = str(e)
                log.warning("job %s page %d failed: %s", job_id, p.index, e)
            job.updated_at = _now()
            db.commit()

        # Final status + output mode.
        done = db.query(Page).filter(Page.job_id == job_id, Page.status == "done").count()
        total = job.pages_total
        job.status = "done" if done == total else ("partial" if done > 0 else "failed")
        job.finished_at = _now()
        if job.output_mode == "cbz" and done > 0:
            job.error = assemble_cbz(job_id) or None
        job.updated_at = _now()
        db.commit()
        log.info("job %s finished: status=%s (%d/%d pages)", job_id, job.status, done, total)
    except Exception as e:
        # Never let the worker die on one bad job.
        try:
            job = db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = str(e)
                job.updated_at = _now()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def assemble_cbz(job_id: int) -> str | None:
    """Zip the job's output pages into <name>.cbz. Returns an error string or None."""
    out_dir = _out_dir(job_id)
    pages = sorted(
        [f for f in os.listdir(out_dir) if f.lower().endswith(_IMG_EXTS)],
        key=_natural_key,
    )
    if not pages:
        return "no output pages to assemble"
    cbz_path = os.path.join(_job_dir(job_id), "translated.cbz")
    with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_STORED) as zf:
        for name in pages:
            zf.write(os.path.join(out_dir, name), arcname=name)
    return None
