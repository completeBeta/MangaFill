"""Opt-in fit instrumentation (Phase 0 of the typesetting plan).

WHY THIS EXISTS
The "big balloon, tiny text" defect kept surviving fixes because nothing recorded
*what the fitter decided or why*. Diagnosis was done by eyeballing rendered pages,
which missed whole classes of defect (a 193-page visual sweep missed page 8). Every
question about a block's size — was the region wrong? was the shape profile clean?
which sizes were rejected, and for what reason? — is answered here with data.

DESIGN RULE: THIS MODULE MUST NEVER BE ABLE TO BREAK A RENDER.
It is instrumentation, so every entry point is exception-proof and tolerates being
called in any order (region resolution happens in `render` before `typeset_page`
calls `begin_page`). A KeyError in here must never surface as a failed page — that
exact mistake is what the Phase-0 test caught.

HOW TO TURN IT ON (per container, no restart needed)
    docker exec <container> touch /tmp/mf_typeset_debug
Outputs:
  * /tmp/mf_typeset_debug.jsonl — one JSON line per record, for machine analysis
    (a companion audit script ranks every block that under-sized; see the CHANGELOG
    entry for v0.27.27)
  * the app log (tailed by the Logs tab) — one summary line per page, so the
    behaviour is visible in the UI without touching the filesystem.

RECORD KINDS
  region     — how a block's lettering region was resolved (branch, chosen box, and
               why a candidate container was rejected)
  block      — one fitted block: region, shape profile, chosen size, and
               `largest_fitting` (the biggest size that fitted the GEOMETRY, before
               any taste rule). `chosen` vs `largest_fitting` is the under-size
               metric the whole-job audit ranks on.
  candidates — the full size table, kept only for blocks that under-sized.
"""
from __future__ import annotations

import json
import os

SENTINEL = "/tmp/mf_typeset_debug"
JSONL = "/tmp/mf_typeset_debug.jsonl"

def _fresh() -> dict:
    """A fresh per-page accumulator.

    A FACTORY, deliberately not a module-level template: a shared dict of mutables gets
    bound by reference, so `sizes`/`branches` would be the same objects on every page —
    counters become cumulative and `sizes` grows without bound. That bug shipped in
    v0.27.28 and was caught by the fitlog tests.
    """
    return {"label": "?", "blocks": 0, "sizes": [], "under": 0, "unmeasurable": 0,
            "degenerate": 0, "recovered": 0, "branches": {},
            # v0.27.31: the lettering-engine change. `narrow_wrap` counts blocks whose
            # winning wrap width was below the balloon's widest row (the "narrow
            # column, bigger glyph" path); `anchored` counts blocks whose ink was
            # shifted off the bounding-box centre to sit on the balloon's centroid.
            "narrow_wrap": 0, "anchored": 0, "anchor_shift": 0,
            # v0.27.33: in-world captions recovered by a vision read on a
            # pure-horizontal page (`captions.recover_captions`).
            "captions": 0,
            # v0.27.41: blocks lettered in the narrower LEGIBILITY FALLBACK face
            # because the configured face could not reach `typeset._LEGIBLE_FLOOR`.
            "fallback": 0}


_page: dict = _fresh()
_meta_written = False


def enabled() -> bool:
    """True when this container has been opted in (sentinel file or env)."""
    try:
        if os.environ.get("MF_FITLOG") == "1":
            return True
        return os.path.exists(SENTINEL)
    except Exception:
        return False


def _ensure() -> None:
    """Make the per-page accumulator usable no matter what order we're called in."""
    global _meta_written
    if not _page:
        _page.update(_fresh())
    for k, v in _fresh().items():
        if k not in _page:
            _page[k] = (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
    if not _meta_written:
        # Record WHICH BUILD produced this dump. Without it a dump is untraceable —
        # exactly the trap that let a container run a mix of file versions unnoticed.
        _meta_written = True
        try:
            from app import __version__
            _write("meta", {"version": __version__, "sentinel": SENTINEL,
                            "jsonl": JSONL})
        except Exception:
            pass


def _write(kind: str, payload: dict) -> None:
    try:
        with open(JSONL, "a") as fh:
            fh.write(json.dumps({"kind": kind, **payload}) + "\n")
    except Exception:
        pass


def reset() -> None:
    """Clear the per-page accumulator — for tests and for marking a fresh run.

    The accumulator is module-global (fine for the single-worker app, which letters one
    page at a time, but it is SHARED across test functions). Tests must call this or
    one test's records leak into another's summary — observed as a page summary
    reporting regions from a different test.
    """
    try:
        _page.clear()
        _page.update({k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                      for k, v in _fresh().items()})
    except Exception:
        pass


def begin_page(label: str) -> None:
    try:
        _page.clear()
        _page.update({k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                      for k, v in _fresh().items()})
        _page["label"] = str(label)
    except Exception:
        pass


def set_page_label(label: str) -> None:
    """Point the accumulator at the current page WITHOUT clearing it.

    `render` records region decisions before `typeset_page` runs, so clearing here
    would drop them. The reset happens at the end of `end_page()` instead.
    """
    try:
        _ensure()
        _page["label"] = str(label)
    except Exception:
        pass


def record_region(bbox, branch: str, region, detail: dict | None = None) -> None:
    """Record how one block's lettering region was resolved."""
    if not enabled():
        return
    try:
        _ensure()
        _page["branches"][str(branch)] = _page["branches"].get(str(branch), 0) + 1
        _write("region", {"page": _page.get("label"), "bbox": [int(v) for v in bbox],
                          "branch": str(branch),
                          "region": [int(v) for v in region] if region else None,
                          "detail": detail or {}})
    except Exception:
        pass


def record_candidates(table: list[dict], chosen: int, largest_fitting, text: str,
                      avail_used: bool) -> None:
    """Keep the size table, but only for blocks that under-sized (keeps the dump small)."""
    if not enabled() or not table:
        return
    try:
        _ensure()
        if largest_fitting is not None and chosen < largest_fitting:
            _write("candidates", {"page": _page.get("label"), "text": text,
                                  "chosen": int(chosen),
                                  "largest_fitting": int(largest_fitting),
                                  "avail_used": bool(avail_used), "table": table})
    except Exception:
        pass


def record_degenerate(bbox, text: str, reason: str, detail: dict | None = None) -> None:
    """A block whose lettering region looks wrong FOR its text (class C).

    Discovered 2026-09-19: a large hand-lettered line can be detected as a thin strip
    (measured on a real page: 197x8 for brush text ~70px tall). The Japanese gets
    erased and the English lands at 8px inside the sliver, leaving a blank area where
    the big Japanese was. Recording it makes the count auditable instead of depending
    on somebody noticing the page.
    """
    if not enabled():
        return
    try:
        _ensure()
        # A RECOVERY is a handled condition (the region was enlarged); a bare
        # thin_region is an unhandled defect. Conflating them made the Logs tab
        # unable to tell "fine" from "broken" — the whole point of the counter.
        _handled = str(reason).startswith(("region_recovered", "recovered"))
        if _handled:
            _page["recovered"] = _page.get("recovered", 0) + 1
            # Surface it: a recovery is a real correction the operator should see, with
            # the before/after boxes, rather than a number buried in the summary.
            try:
                from app.services.logging import get_logger
                get_logger("fit").info(
                    "FITRECOVER page=%s reason=%s box=%s old=%s new=%s",
                    _page.get("label"), reason, list(bbox),
                    (detail or {}).get("old"), (detail or {}).get("new"))
            except Exception:
                pass
        else:
            _page["degenerate"] = _page.get("degenerate", 0) + 1
        _write("degenerate", {"page": _page.get("label"),
                              "bbox": [int(v) for v in bbox], "text": text,
                              "reason": str(reason), "detail": detail or {}})
    except Exception:
        pass


def record_erase(bbox, pixels: int, boxes: int, detail: dict | None = None) -> None:
    """What was erased for a block. Blank-area defects need erase + lettering together
    to be diagnosable: a big erase with tiny lettering IS the class-C symptom."""
    if not enabled():
        return
    try:
        _ensure()
        _write("erase", {"page": _page.get("label"), "bbox": [int(v) for v in bbox],
                         "pixels": int(pixels), "boxes": int(boxes),
                         "detail": detail or {}})
    except Exception:
        pass


def record_block(bbox, text, region, avail_max, avail_rows_usable, max_w, max_h,
                 chosen, largest_fitting, avail_used, n_lines,
                 trace_ok: bool = True, wrap=None, frac=None, shape_tol=None,
                 anchor_dy=None, anchor_dx=None, face=None,
                 fallback: bool = False) -> None:
    """One fitted block. `chosen` vs `largest_fitting` is the audit metric.

    `trace_ok=False` means the fitter bailed before tracing (a degenerate region), so
    `largest_fitting` is unknown for this block — the audit counts those separately
    instead of inventing a gap from stale numbers.

    `wrap`/`frac`/`shape_tol` say HOW the fit was reached (v0.27.31): the wrap width
    that won, it as a fraction of the balloon's widest row, and the outline tolerance
    in force. `anchor_dy` is how far the ink was moved off the bounding-box centre to
    land on the balloon's centroid. Without these a dump cannot tell a block that was
    lettered to the widest row from one lettered to a narrow column.
    """
    if not enabled():
        return
    try:
        _ensure()
        _page["blocks"] += 1
        _page["sizes"].append(int(chosen))
        if not trace_ok:
            _page["unmeasurable"] = _page.get("unmeasurable", 0) + 1
        elif largest_fitting is not None and chosen < largest_fitting:
            _page["under"] += 1
        if frac is not None and float(frac) < 1.0:
            _page["narrow_wrap"] = _page.get("narrow_wrap", 0) + 1
        _dxy = [int(v) for v in (anchor_dx, anchor_dy) if v is not None]
        if any(v != 0 for v in _dxy):
            _page["anchored"] = _page.get("anchored", 0) + 1
            _page["anchor_shift"] = _page.get("anchor_shift", 0) + max(abs(v) for v in _dxy)
        if fallback:
            _page["fallback"] = _page.get("fallback", 0) + 1
        _write("block", {
            "page": _page.get("label"), "bbox": [int(v) for v in bbox], "text": text,
            "region": [int(v) for v in region] if region else None,
            "avail_used": bool(avail_used),
            "avail_max": int(avail_max) if avail_max is not None else None,
            "avail_rows_usable": (int(avail_rows_usable)
                                  if avail_rows_usable is not None else None),
            "max_w": int(max_w), "max_h": int(max_h), "chosen": int(chosen),
            "largest_fitting": (int(largest_fitting)
                                if largest_fitting is not None else None),
            "n_lines": int(n_lines), "trace_ok": bool(trace_ok),
            "wrap": int(wrap) if wrap is not None else None,
            "frac": float(frac) if frac is not None else None,
            "shape_tol": float(shape_tol) if shape_tol is not None else None,
            "anchor_dy": int(anchor_dy) if anchor_dy is not None else None,
            "anchor_dx": int(anchor_dx) if anchor_dx is not None else None,
            "face": face, "fallback": bool(fallback),
        })
    except Exception:
        pass


def end_page() -> None:
    """Emit the per-page summary into the app log (visible in the Logs tab), then reset.

    Resetting here (rather than relying on a later `begin_page`) is deliberate: region
    resolution happens in `render` BEFORE `typeset_page` runs, so anything that clears
    the accumulator mid-page silently discards those region records — which is exactly
    the bug this ordering avoids.
    """
    if not enabled():
        # Still reset: the accumulator must not carry into the next page just because
        # logging is off for this one (toggling the sentinel mid-run otherwise leaves
        # stale counts behind).
        begin_page("?")
        return
    try:
        _ensure()
        if _page.get("blocks") or _page.get("branches"):
            sizes = _page.get("sizes") or [0]
            from app.services.logging import get_logger
            get_logger("fit").info(
                "FITLOG page=%s blocks=%d regions=%d size_min=%d size_max=%d "
                "under_sized=%d unmeasurable=%d degenerate=%d recovered=%d "
                "narrow_wrap=%d anchored=%d anchor_shift=%d captions=%d fallback=%d "
                "branches=%s",
                _page.get("label"), _page.get("blocks", 0),
                sum(_page["branches"].values()), min(sizes), max(sizes),
                _page.get("under", 0), _page.get("unmeasurable", 0),
                _page.get("degenerate", 0), _page.get("recovered", 0),
                _page.get("narrow_wrap", 0), _page.get("anchored", 0),
                _page.get("anchor_shift", 0), _page.get("captions", 0),
                _page.get("fallback", 0),
                dict(_page["branches"]),
            )
    except Exception:
        pass
    finally:
        begin_page("?")


def record_caption(bbox, jp: str, en: str, kind: str, applied: bool,
                   reason: str = "") -> None:
    """One in-world-caption decision (v0.27.33), applied or not.

    The house rule for a recovery is that a NON-firing one must be as visible as a
    firing one — that is what cost the most time on class C. So a rejected caption
    records its reason (wrong `kind`, a credit marker, a hallucinated length) exactly
    like an accepted one records the text it lettered.
    """
    if not enabled():
        return
    try:
        _ensure()
        if applied:
            _page["captions"] = _page.get("captions", 0) + 1
        _write("caption", {"page": _page.get("label"), "bbox": [int(v) for v in bbox],
                           "jp": jp, "en": en, "verdict": kind,
                           "applied": bool(applied), "reason": reason})
        if applied:
            try:
                from app.services.logging import get_logger

                get_logger("fit").info("CAPTION page=%s bbox=%s jp=%r en=%r",
                                       _page.get("label"), list(bbox), jp, en)
            except Exception:
                pass
    except Exception:
        pass
