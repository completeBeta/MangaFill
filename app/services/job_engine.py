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
from datetime import datetime, timedelta, timezone

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
    """The model's API key (empty string if none is configured)."""
    return (m.api_key or "") if m is not None else ""


def _job_dir(job_id: int) -> str:
    return os.path.join(settings.jobs_dir, str(job_id))


def _orig_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "original")


def _out_dir(job_id: int) -> str:
    return os.path.join(_job_dir(job_id), "output")


def _delete_job_files(job_id: int) -> None:
    """Remove a job's on-disk directory (original + output + archives)."""
    d = _job_dir(job_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


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


def _save_output(img, out_dir: str, original_path: str) -> str:
    """Save a rendered page preserving the ORIGINAL filename + extension.

    The user wants output names to mirror the input, not be renumbered to
    `0000.png`. PIL infers the format from the extension; JPEG needs RGB.
    """
    base = os.path.basename(original_path)
    out_path = os.path.join(out_dir, base)
    if base.lower().endswith((".jpg", ".jpeg")) and img.mode != "RGB":
        img = img.convert("RGB")
    img.save(out_path)
    return out_path


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
        font_id = get_setting(db, "font")
        gpu_url = get_setting(db, "gpu_worker_url").strip()
        model = m.name if m else ""
        base_url = m.base_url if m else ""
        key = _resolve_key(m)
        out_dir = _out_dir(job_id)
        os.makedirs(out_dir, exist_ok=True)
        log.info("job %s: model=%s dry_run=%s", job_id, model, dry_run)

        pages = db.query(Page).filter(Page.job_id == job_id).order_by(Page.index).all()
        stopped = False

        # Source language: an explicit setting wins; "auto" detects it once from
        # the first page and reuses it for the whole job (per-page detection
        # would re-OCR every page's probe). Any failure falls back to Japanese.
        lang = (get_setting(db, "source_lang") or "auto").strip()
        if lang == "auto" and pages:
            try:
                from PIL import Image
                from app.pipeline.ocr_multilingual import detect_language

                with Image.open(pages[0].original_path) as _first:
                    first = _first.convert("RGB")
                lang = detect_language(first)
                log.info("job %s: auto-detected source language=%s", job_id, lang)
            except Exception as e:
                log.warning("job %s: language detection failed (%s) — defaulting to ja",
                            job_id, e)
                lang = "ja"
        if lang not in ("ja", "ko", "zh"):
            lang = "ja"

        def _progress(stage: str) -> None:
            # Live stage for the dashboard's granular progress bar. Committing on
            # every stage change is a handful of writes per page — cheap under WAL.
            job.stage = stage
            job.updated_at = _now()
            db.commit()

        for p in pages:
            # Respect stop/pause set from the API mid-run (fresh read from DB).
            try:
                db.refresh(job)
            except Exception:
                return  # job row deleted (e.g. clear-all) — bail out
            if job.status in ("cancelled", "paused"):
                stopped = True
                break
            if p.status == "done":
                continue  # resume: skip pages already translated
            p.status = "running"
            job.stage = "detect"
            db.commit()
            try:
                img, blocks, pt, ct = render_translated_page(
                    p.original_path, model, key, base_url, dry_run=dry_run,
                    font_id=font_id, gpu_worker_url=gpu_url,
                    progress_cb=_progress, lang=lang,
                )
                out_path = _save_output(img, out_dir, p.original_path)
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

        if stopped:
            job.stage = ""
            db.commit()
            log.info("job %s stopped early (status=%s)", job_id, job.status)
            return

        # Final status + output mode.
        done = db.query(Page).filter(Page.job_id == job_id, Page.status == "done").count()
        total = job.pages_total
        job.status = "done" if done == total else ("partial" if done > 0 else "failed")
        job.stage = ""
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


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def purge_old_jobs(db, days: int = 7) -> int:
    """Delete jobs (DB rows + on-disk files) whose created_at is older than `days`.

    Runs periodically from the worker. Returns the number purged.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    purged = 0
    for job in db.query(Job).all():
        created = _parse_dt(job.created_at)
        if created is not None and created < cutoff:
            _delete_job_files(job.id)
            db.delete(job)
            purged += 1
    if purged:
        db.commit()
    return purged


def clear_all_jobs(db) -> int:
    """Delete every job (DB rows + on-disk files). Returns the count removed."""
    jobs = db.query(Job).all()
    for job in jobs:
        _delete_job_files(job.id)
        db.delete(job)
    db.commit()
    return len(jobs)
