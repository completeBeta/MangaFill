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

_EMPTY = {"label": "?", "blocks": 0, "sizes": [], "under": 0, "branches": {}}
_page: dict = dict(_EMPTY)


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
    if not _page:
        _page.update(_EMPTY)
    for k, v in _EMPTY.items():
        if k not in _page:
            _page[k] = (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)


def _write(kind: str, payload: dict) -> None:
    try:
        with open(JSONL, "a") as fh:
            fh.write(json.dumps({"kind": kind, **payload}) + "\n")
    except Exception:
        pass


def begin_page(label: str) -> None:
    try:
        _page.clear()
        _page.update({"label": str(label), "blocks": 0, "sizes": [], "under": 0,
                      "branches": {}})
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


def record_block(bbox, text: str, region, avail_max, avail_rows_usable, max_w, max_h,
                 chosen: int, largest_fitting, avail_used: bool, n_lines: int) -> None:
    """One fitted block. `chosen` vs `largest_fitting` is the audit metric."""
    if not enabled():
        return
    try:
        _ensure()
        _page["blocks"] += 1
        _page["sizes"].append(int(chosen))
        if largest_fitting is not None and chosen < largest_fitting:
            _page["under"] += 1
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
            "n_lines": int(n_lines),
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
        return
    try:
        _ensure()
        if _page.get("blocks") or _page.get("branches"):
            sizes = _page.get("sizes") or [0]
            from app.services.logging import get_logger
            get_logger("fit").info(
                "FITLOG page=%s blocks=%d regions=%d size_min=%d size_max=%d "
                "under_sized=%d branches=%s",
                _page.get("label"), _page.get("blocks", 0),
                sum(_page["branches"].values()), min(sizes), max(sizes),
                _page.get("under", 0), dict(_page["branches"]),
            )
    except Exception:
        pass
    finally:
        begin_page("?")
