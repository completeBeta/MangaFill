"""Job engine — turn an uploaded job into translated pages.

Single worker: processes one page at a time, writes progress + per-page state to
SQLite as it goes (so the dashboard can show live progress). The headless pipeline
(`app.pipeline.render.render_translated_page`) does the actual detect → OCR →
translate → inpaint → typeset work; this module owns upload ingest, the page loop,
persistence, and output-mode assembly.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from datetime import datetime, timezone

from app.config import settings
from app.db import SessionLocal
from app.models import Job, Model, Page, TextBlock
from app.pipeline.render import render_translated_page
from app.services.logging import get_logger
from app.services.pricing import compute_cost
from app.settings_store import default_model, get_model, get_setting

log = get_logger("job_engine")

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_CHUNK = 1024 * 1024  # 1 MB streaming chunks — never read a whole upload into RAM


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _natural_key(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def resolve_translation(db, model_id=None) -> tuple[Model | None, bool]:
    """Return (model, dry_run) for a job's model.

    Resolves `model_id` against the user's model list (falls back to the first
    model). The caller derives name/base_url/api_key + pricing from the returned
    `Model` object.
    """
    m = get_model(db, model_id) or default_model(db)
    dry_run = get_setting(db, "dry_run") == "true"
    return m, dry_run


def _resolve_key(m: Model | None) -> str:
    """The model's API key, with a local-dev fallback to the raw key file."""
    if m is not None and m.api_key:
        return m.api_key
    env = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _job_dir(job_id: int) -> str:
    return os.path.join(settings.jobs_dir, str(job_id))


def _orig_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "original")


def _out_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "output")


def _stream_to(path: str, src) -> None:
    """Copy an open file-like `src` to `path` in 1 MB chunks.

    Never calls `src.read()` without a size — reading a whole upload into RAM is
    what OOM-killed Subber on this swap-less VM. Chunked streaming keeps peak
    memory at ~1 MB regardless of upload size.
    """
    with open(path, "wb") as out:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            out.write(chunk)


def ingest_upload(job_id: int, files: list) -> tuple[list[str], str]:
    """Save uploaded files (images and/or a .cbz/.zip) to the job's original dir,
    in natural reading order. Returns (page_paths, source_format).

    Archives are staged to disk and expanded member-by-member via streaming — the
    whole archive is never held in RAM.
    """
    orig = _orig_dir(job_id)
    os.makedirs(orig, exist_ok=True)
    paths: list[str] = []
    source_format = "folder"

    for f in files:
        name = f.filename or "page"
        low = name.lower()
        if low.endswith(".cbz") or low.endswith(".zip"):
            source_format = "cbz" if low.endswith(".cbz") else "zip"
            archive_path = os.path.join(orig, os.path.basename(name))
            _stream_to(archive_path, f.file)
            with zipfile.ZipFile(archive_path) as zf:
                for member in sorted(zf.namelist(), key=_natural_key):
                    if member.lower().endswith(_IMG_EXTS):
                        out = os.path.join(orig, os.path.basename(member))
                        with zf.open(member) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst, _CHUNK)
                        paths.append(out)
            os.remove(archive_path)  # expanded — drop the staging copy
        elif low.endswith(_IMG_EXTS):
            out = os.path.join(orig, name)
            _stream_to(out, f.file)
            paths.append(out)

    paths.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return paths, source_format


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

        m, dry_run = resolve_translation(db, job.model_id)
        model = m.name if m else ""
        base_url = m.base_url if m else ""
        key = _resolve_key(m)
        out_dir = _out_dir(job_id)
        os.makedirs(out_dir, exist_ok=True)
        log.info("job %s: model=%s dry_run=%s", job_id, model, dry_run)

        pages = db.query(Page).filter(Page.job_id == job_id).order_by(Page.index).all()
        for p in pages:
            p.status = "running"
            db.commit()
            try:
                img, blocks, pt, ct = render_translated_page(
                    p.original_path, model, key, base_url, dry_run=dry_run
                )
                out_path = os.path.join(out_dir, f"{p.index:04d}.png")
                img.save(out_path)
                p.output_path = out_path
                p.status = "done"
                p.error = None
                job.blocks_found += len(blocks)
                job.blocks_ok += sum(1 for b in blocks if b.translation)
                job.tokens_used += (pt or 0) + (ct or 0)
                job.cost_usd += compute_cost(m, pt or 0, ct or 0)
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
        if done > 0:
            job.error = _assemble(job_id, job.output_mode, job.source_format) or None
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


def _assemble(job_id: int, output_mode: str, source_format: str) -> str | None:
    """Assemble the output per the chosen mode. Returns an error string or None.

    - "cbz"    → always a .cbz
    - "mirror" → match the input format (.cbz → .cbz, .zip → .zip, folder → none)
    - "folder" → leave as-is (no assembly)
    """
    if output_mode == "cbz":
        return assemble_archive(job_id, "cbz")
    if output_mode == "mirror" and source_format in ("cbz", "zip"):
        return assemble_archive(job_id, source_format)
    return None


def assemble_archive(job_id: int, ext: str) -> str | None:
    """Zip the job's output pages into translated.<ext>. Returns an error or None."""
    out_dir = _out_dir(job_id)
    pages = sorted(
        [f for f in os.listdir(out_dir) if f.lower().endswith(_IMG_EXTS)],
        key=_natural_key,
    )
    if not pages:
        return "no output pages to assemble"
    arc_path = os.path.join(_job_dir(job_id), f"translated.{ext}")
    with zipfile.ZipFile(arc_path, "w", zipfile.ZIP_STORED) as zf:
        for name in pages:
            zf.write(os.path.join(out_dir, name), arcname=name)
    return None
