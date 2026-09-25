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
from app.pipeline import engine as engine_client
from app.pipeline import second_pass
from app.pipeline.render import render_translated_page
from app.pipeline.translate import ProviderError
from app.services import audit
from app.services.logging import get_logger, lifecycle
from app.services.pricing import compute_cost
from app.settings_store import default_model, get_model, get_setting

log = get_logger("job_engine")

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_CHUNK = 1024 * 1024  # 1 MB streaming chunks — never read a whole upload into RAM
# Vertical-scroll webtoon lookahead: how much of the next page's top to stitch
# onto the current page so a bubble cut at the page boundary is seen whole.
LOOKAHEAD_PX = 500


def _num_setting(db, key: str, default: float) -> float:
    """Read a numeric setting, falling back to `default` when absent/garbage."""
    try:
        return float(get_setting(db, key))
    except (KeyError, TypeError, ValueError):
        return float(default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _natural_key(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _load_lookahead(path: str, px: int):
    """Load the top `px` rows of the next page as an RGB uint8 array (or None)."""
    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            return np.asarray(im.crop((0, 0, w, min(px, h))))
    except Exception:
        return None


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
    what OOM-kills this app on a swap-less VM. Chunked streaming keeps peak
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
        # Translation-provider resilience (Settings tab). Lax by default: a
        # degraded provider can take minutes to answer, so a slow-but-alive call
        # is given room; only a real outage (repeated failures) stops the job.
        llm_timeout = _num_setting(db, "llm_timeout", 300.0)
        llm_max_retries = int(_num_setting(db, "llm_max_retries", 2))
        provider_fail_limit = max(1, int(_num_setting(db, "provider_fail_limit", 3)))
        # In-world caption recovery on pure-horizontal pages (Settings tab). Read once
        # per job, like the other provider settings.
        caption_raw = (get_setting(db, "caption_vlm") or "").strip().lower()
        caption_vlm = caption_raw in ("1", "true", "yes")
        model = m.name if m else ""
        base_url = m.base_url if m else ""
        key = _resolve_key(m)
        out_dir = _out_dir(job_id)
        os.makedirs(out_dir, exist_ok=True)
        log.info("job %s: model=%s dry_run=%s llm_timeout=%.0fs retries=%d provider_fail_limit=%d caption_vlm=%s(%r)",
                 job_id, model, dry_run, llm_timeout, llm_max_retries, provider_fail_limit,
                 caption_vlm, caption_raw)

        pages = db.query(Page).filter(Page.job_id == job_id).order_by(Page.index).all()
        stopped = False

        # Source language: an explicit setting wins; "auto" detects it once from
        # the first page and reuses it for the whole job (per-page detection
        # would re-OCR every page's probe). Any failure falls back to Japanese.
        lang = (get_setting(db, "source_lang") or "auto").strip()
        # ENGINE MODE: the engine detects the source language itself (its config takes
        # source_lang and auto-detects when it is empty). Loading PaddleOCR here for our own
        # guess is what OOM-killed this app on the zh jobs: uvicorn reached anon-rss 4.0 GB
        # on a 7.9 GB host while the CPU engine was also resident, and the kernel killed it
        # mid-job (see `dmesg`). In engine mode we skip our detection and send source_lang
        # only when the user picked one explicitly.
        _engine_handles_lang = bool(get_setting(db, "engine_enabled") == "true"
                                    and (get_setting(db, "engine_url") or settings.engine_url).strip())
        if lang == "auto" and pages and not _engine_handles_lang:
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
        # The measured per-language presets are keyed on the SOURCE language, and upstream
        # never reports the language it detected internally (checked 2026-09-24: its API
        # returns translations and boxes, the result folder carries only the target
        # language, and its own "Detected source language" log line is commented out). So
        # when the user left the language on auto and presets are on, probe it once — in a
        # CHILD process, because doing it in-process is what OOM-killed this app at
        # 4.05 GB while the engine's models were resident (app/pipeline/lang_probe.py).
        preset_mode = (get_setting(db, "engine_preset_mode") or "per-language").strip()
        # WHO DRAWS THE TRANSLATED TEXT (v0.30.0, option 1): "ours" = the
        # hybrid — the engine detects/OCRs/translates and THIS app letters the result
        # with the typesetter that made v0.27.x look finished; "upstream" = the engine
        # renders the page itself (the v0.29 behaviour).
        engine_lettering = (get_setting(db, "engine_lettering") or "ours").strip()
        # SECOND PASS (v0.30.1): the engine only reports what it TRANSLATED, so text its
        # detector missed (part of a line, vertical kana) is never erased or lettered. With
        # this on, our own detector finds those and our LLM translates them.
        second_pass_on = (get_setting(db, "engine_second_pass") or "on").strip() == "on"
        # Did the USER choose the source language, or is it ours to work out? This decides
        # whether the engine's own answer may override `lang` per page (see the hybrid block):
        # a user setting is an instruction, an auto/probe/first-page guess is not.
        explicit_lang = lang in ("ja", "ko", "zh")
        lang_origin = "explicit setting" if explicit_lang else ""
        if lang == "auto" and _engine_handles_lang and preset_mode != "off" and pages:
            from app.pipeline import lang_probe

            probed, why = lang_probe.probe(pages[0].original_path)
            if probed:
                lang = probed
                lang_origin = why
                log.info("job %s: %s", job_id, why)
            else:
                lifecycle("job %s: per-language presets skipped for this job (%s) — "
                          "the engine's defaults will be used", job_id, why)
        if lang not in ("ja", "ko", "zh"):
            # Our own pipeline needs a concrete language (it falls back to ja, as it always
            # has). In engine mode the engine detects the source language itself, so this
            # value never reaches it — keep it empty rather than asserting "ja" for a
            # manhua, which is what made the logs claim JPN on a Chinese job.
            lang = "" if _engine_handles_lang else "ja"

        # ---- ENGINE mode (option A, 2026-09-23) --------------------------------
        # When an engine URL is configured and the switch is on, the ENGINE runs the
        # pipeline for every page and this app is the shell (jobs, queue, viewer,
        # logs, fonts, CBZ/PDF). Upstream's translator keys come from the engine
        # container's environment, so our model/key rows stay as they are.
        engine_client.configure(url=(get_setting(db, "engine_url") or settings.engine_url).strip(),
                                enabled_flag=(get_setting(db, "engine_enabled") == "true"))
        engine_on = engine_client.enabled()
        engine_cfg = engine_client.build_config({
            "detector": get_setting(db, "engine_detector"),
            "detection_size": get_setting(db, "engine_detection_size"),
            "box_threshold": get_setting(db, "engine_box_threshold"),
            "unclip_ratio": get_setting(db, "engine_unclip_ratio"),
            "render_direction": get_setting(db, "engine_render_direction"),
            "inpainter": get_setting(db, "engine_inpainter"),
            "inpainting_size": get_setting(db, "engine_inpainting_size"),
            "mask_dilation_offset": get_setting(db, "engine_mask_dilation_offset"),
            "target_lang": get_setting(db, "target_lang"),
            "source_lang": {"ja": "JPN", "ko": "KOR", "zh": "CHS"}.get(lang, ""),
        }, source_lang=lang, preset_mode=preset_mode) if engine_on else None
        engine_preset = engine_client.preset_name(lang, preset_mode) if engine_on else ""
        lifecycle("job %s: engine=%s url=%s dry_run=%s lettering=%s preset=%s config=%s",
                  job_id, "ON" if engine_on else "off", engine_client.base_url() or "-",
                  dry_run, engine_lettering if engine_on else "-",
                  engine_preset or f"none (mode={preset_mode})", engine_cfg)
        if engine_on:
            # Say WHICH language the preset decision was based on, so a surprising config
            # in the log can always be traced back to a setting or a probe result.
            lifecycle("job %s: source language=%s (%s) -> %s",
                      job_id, lang or "engine auto-detect", lang_origin or "engine auto-detect",
                      engine_preset or "engine defaults")
        if engine_on:
            log.info("job %s: ENGINE mode — pages are translated by %s", job_id,
                     engine_client.base_url())

        def _progress(stage: str) -> None:
            # Live stage for the dashboard's granular progress bar. Committing on
            # every stage change is a handful of writes per page — cheap under WAL.
            job.stage = stage
            job.updated_at = _now()
            db.commit()

        carryover: list = []  # patches handed from the previous page (boundary bubbles)
        do_lookahead = lang in ("ko", "zh")
        provider_streak = 0  # consecutive page failures caused by the LLM provider
        for idx, p in enumerate(pages):
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
                if engine_on:
                    # ENGINE mode: one call per page; the engine returns the finished
                    # page. dry_run means "don't spend" — the page is passed through
                    # untouched, exactly like the local pipeline's dry run does.
                    if dry_run:
                        from PIL import Image as _PILImage  # local: module-level import is conditional
                        with _PILImage.open(p.original_path) as _im:
                            img = _im.convert("RGB").copy()
                        blocks, pt, ct, carryover = [], 0, 0, []
                        lifecycle("job %s page %d: dry-run (engine not called)", job_id, p.index)
                    else:
                        _progress("engine")
                        if engine_lettering == "ours":
                            # ---- HYBRID (v0.30.0, option 1) -----------------
                            # The engine detects + OCRs + translates; THIS app letters
                            # the result with its own typesetter, because that is the
                            # part that was already finished (text fitted to the balloon
                            # and filling it). We call the engine's JSON endpoint, which
                            # returns regions + source text + translations and renders
                            # nothing, then hand those regions straight to our own render
                            # path — the same code the non-engine pipeline uses.
                            regions, engine_lang = engine_client.translate_page_regions(
                                p.original_path, engine_cfg,
                                tag=f"job{job_id}/p{p.index}")
                            if engine_lang and not explicit_lang and engine_lang != lang:
                                # The engine just read THIS page and says what it is. Without
                                # this our render path falls back to ja, and its colour-page
                                # skip then returns the page untouched (measured: job 29, a
                                # colour Korean webtoon page, 2 regions read → 0 blocks
                                # lettered; and measured again in prod 2026-09-25: a 2-page job
                                # whose first page is Japanese left `lang="ja"` for the SECOND
                                # page, so the colour Korean page was silently blanked).
                                # An explicit source_lang setting is still the user's call.
                                if lang:
                                    lifecycle("job %s page %d: engine reads source=%s "
                                              "(was %s) — using the engine's answer",
                                              job_id, p.index, engine_lang, lang)
                                lang = engine_lang
                            # ---- SECOND PASS (v0.30.1) ------------------------------
                            # Upstream only returns regions it TRANSLATED, so whatever its
                            # detector missed — the tail of a line, vertical kana — is never
                            # erased and sits on the page under our lettering (the ko
                            # test page 001: `대한민국매출규모` read, `1위를 다리고 이는…` missed).
                            # Our own detector finds those and our LLM translates them; from
                            # here on they are ordinary regions. Never fatal: a failure or a
                            # skip returns [] and the page is lettered from the engine's
                            # regions alone, exactly as in v0.30.0.
                            if second_pass_on and lang in ("ja", "ko", "zh"):
                                try:
                                    _extra, _drop, _why = second_pass.augment_regions(
                                        p.original_path, lang, regions,
                                        model=model, api_key=key, base_url=base_url,
                                        dry_run=dry_run, timeout=llm_timeout,
                                        max_retries=llm_max_retries, log=log.info)
                                except Exception as _e:  # noqa: BLE001
                                    _extra, _drop, _why = [], set(), "%s: %s" % (
                                        type(_e).__name__, _e)
                                if _drop:
                                    # Regions the second pass absorbed into a merged one: keeping
                                    # them would put two English strings on the same line.
                                    regions = [r for i, r in enumerate(regions) if i not in _drop]
                                regions = regions + _extra
                                lifecycle("job %s page %d: second pass: %s (regions now %d)",
                                          job_id, p.index, _why, len(regions))
                            lifecycle("job %s page %d: engine regions=%d source=%s "
                                      "(hybrid: our lettering)", job_id, p.index,
                                      len(regions), engine_lang or lang or "?")
                            lookahead = None
                            if do_lookahead and idx + 1 < len(pages):
                                lookahead = _load_lookahead(pages[idx + 1].original_path,
                                                            LOOKAHEAD_PX)
                            img, blocks, pt, ct, carryover = render_translated_page(
                                p.original_path, model, key, base_url,
                                font_id=font_id, progress_cb=_progress, lang=lang,
                                lookahead=lookahead, carryover=carryover,
                                llm_timeout=llm_timeout, llm_max_retries=llm_max_retries,
                                engine_regions=regions,
                            )
                            lifecycle("job %s page %d: hybrid done (blocks=%d)",
                                      job_id, p.index, len(blocks))
                        else:
                            img = engine_client.translate_page(
                                p.original_path, engine_cfg,
                                tag=f"job{job_id}/p{p.index}")
                            blocks, pt, ct, carryover = [], 0, 0, []
                            lifecycle("job %s page %d: engine done", job_id, p.index)
                else:
                    lookahead = None
                    if do_lookahead and idx + 1 < len(pages):
                        lookahead = _load_lookahead(pages[idx + 1].original_path, LOOKAHEAD_PX)
                    img, blocks, pt, ct, carryover = render_translated_page(
                        p.original_path, model, key, base_url, dry_run=dry_run,
                        font_id=font_id, gpu_worker_url=gpu_url,
                        progress_cb=_progress, lang=lang,
                        lookahead=lookahead, carryover=carryover,
                        llm_timeout=llm_timeout, llm_max_retries=llm_max_retries,
                        caption_vlm=caption_vlm,
                    )
                out_path = _save_output(img, out_dir, p.original_path)
                p.output_path = out_path
                p.status = "done"
                p.error = None
                job.blocks_found += len(blocks)
                job.blocks_ok += sum(1 for b in blocks if b.translation)
                job.tokens_used += (pt or 0) + (ct or 0)
                job.cost_usd += compute_cost(m, pt or 0, ct or 0)
                # persist detected blocks (for the side-by-side viewer / logs).
                # Clear any rows from a prior run of this page first, so a
                # re-render replaces them instead of accumulating stale
                # duplicate blocks (which pollute the viewer and overlap scans).
                prev_blocks = db.query(TextBlock).filter(TextBlock.page_id == p.id).count()
                if not engine_on and not blocks and prev_blocks:
                    # A re-render that suddenly finds NO text where the previous run
                    # found some is almost always a mis-detection (wrong source
                    # language, dead GPU worker) — and it has just overwritten a
                    # translated page with the untranslated original. Say so loudly
                    # instead of finishing silently "done".
                    log.error("job %s page %d rendered 0 blocks but previously had %d — the "
                              "page was overwritten with the UNTRANSLATED original; check the "
                              "source-language setting, the GPU worker, and the provider",
                              job_id, p.index, prev_blocks)
                db.query(TextBlock).filter(TextBlock.page_id == p.id).delete()
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
                provider_streak = 0
                log.info("job %s page %d/%d done (%d blocks, %d translated)",
                         job_id, job.pages_done, job.pages_total, len(blocks),
                         sum(1 for b in blocks if b.translation))
            except Exception as e:
                p.status = "failed"
                p.error = str(e)
                if isinstance(e, ProviderError):
                    # The LLM endpoint itself failed. Retries already happened
                    # inside translate_lines, so a failure here means the
                    # provider is genuinely down: say so loudly and stop rather
                    # than churning through every remaining page untranslated.
                    provider_streak += 1
                    log.error("job %s page %d failed — translation provider error (%d in a row): %s",
                              job_id, p.index, provider_streak, e)
                    if provider_streak >= provider_fail_limit:
                        job.status = "failed"
                        job.error = (
                            f"Translation provider unavailable — {provider_streak} pages failed "
                            f"in a row ({e}). Job stopped. Check the model endpoint / API key in "
                            f"Settings, then Resume (already-finished pages are skipped)."
                        )
                        job.updated_at = _now()
                        db.commit()
                        log.error("job %s stopped: translation provider unavailable (%s)",
                                  job_id, e)
                        stopped = True
                        break
                else:
                    provider_streak = 0
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
        failed_pages = db.query(Page).filter(Page.job_id == job_id, Page.status == "failed").all()
        total = job.pages_total
        job.status = "done" if done == total else ("partial" if done > 0 else "failed")
        job.stage = ""
        job.finished_at = _now()
        if done > 0:
            job.error = _assemble(job_id, job.output_mode, job.source_format) or job.error
        # Never finish a job silently: if any page failed, say which and why.
        # (A silent "partial" finishing with no explanation is exactly what made a
        # provider outage look like a stalled job.)
        if failed_pages and not job.error:
            first = failed_pages[0]
            job.error = (
                f"{len(failed_pages)} of {total} page(s) failed"
                + (f" — first error (page {first.index + 1}): {first.error}" if first.error else "")
            )
        job.updated_at = _now()
        db.commit()
        log.info("job %s finished: status=%s (%d/%d pages, %d failed)",
                 job_id, job.status, done, total, len(failed_pages))
        # System-side record: the engine finished a job on its own. Paired with the
        # "user" entries, the log now shows who did what — not just the requests.
        audit.record("job.finished", target=f"job {job_id}", actor="system",
                     detail={"name": job.name, "status": job.status,
                             "pages_done": done, "pages_total": total,
                             "pages_failed": len(failed_pages)})
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
