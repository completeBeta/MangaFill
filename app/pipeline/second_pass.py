"""The hybrid's SECOND PASS: add the text the engine never reported — and nothing else.

WHY (measured 2026-09-25, the Korean test page 001)
Upstream's detector read `대한민국매출규모` and stopped; the tail of that same line
(`1위를 다리고 이는…`) was never a region, so it was never erased or translated and sat
visible on the white plate under our English. Upstream's JSON only returns regions it
TRANSLATED, so the misses cannot be read out of its response — our own detector has to find
them (manga-ocr for ja, PaddleOCR for ko/zh).

WHAT THIS PASS DOES NOW: ADDS ONLY (changed 2026-09-25, v0.30.6)
Earlier forms MERGED a recovered box into the engine region it overlapped — union the boxes,
translate our read of the line, and hand the engine's region back to be dropped. Every one of
those forms was a defect surface, and the last one was measured bad in a new way: our read of
a line the engine had read PARTLY is a partly-garbled read (that is why the engine stopped
where it did), and the model answered `대한민국매출규모 1이르다리고이느` with
"South Korea sales scale 1ireudarigoineu" — romanised garbage, lettered over the plate, in
place of the engine's own clean "South Korea sales scale".

So the engine's read is now AUTHORITATIVE for its own boxes: any recovered box that touches an
engine region (whether it sits inside it or extends past it) is COVERED — nothing is dropped,
nothing is re-translated, and an unreadable tail is left exactly as upstream alone leaves it
("output it as it is"). Only a box that touches NO engine region is a genuine miss, and those
are the ones this pass adds. One line, one owner, no fabrication, and no way for this pass to
remove coverage that the engine already had (the v0.30.1 failure).

Deliberately conservative, because this runs inside a job on a memory-tight host:
  * a MEMORY GUARD — our OCR is what OOM-killed this box before, so the pass is skipped (with
    the reason logged) unless MEMORY_MIN_GB is free;
  * `dry_run` never calls the LLM;
  * a recovered region with no usable English (the prompt hands unreadable text back as the
    source) is dropped, never drawn;
  * ANY failure returns ([], set(), reason) — the page is then lettered from the engine's
    regions exactly as before. A second pass must never be able to break a page.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .lang_probe import mem_available_gb
from .types import TextBlock

# Our ko/zh OCR peaks around 1.9 GB (measured for the language probe) and the engine may also
# be resident, so below this the pass is skipped rather than risking the engine's models.
MEMORY_MIN_GB = 2.5


def _box_region(b: TextBlock) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in b.bbox)
    return x, y, x + w, y + h


def _inter(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _region_box(region: dict) -> tuple[int, int, int, int]:
    """An engine region dict as an (x0, y0, x1, y1) box."""
    return (int(region["x0"]), int(region["y0"]), int(region["x1"]), int(region["y1"]))


def _orientation(w: int, h: int) -> str:
    if w <= 16 and h > w * 2:
        return "furigana"
    if h > w * 1.5:
        return "vertical"
    return "horizontal"


def _letterable(b: TextBlock) -> bool:
    """True when this recovered block carries English worth drawing on the page.

    An unreadable read comes back from the LLM AS THE SOURCE (upstream's rule), which leaves
    `translation` empty here — not letterable, and it is then simply not drawn.
    """
    text = (b.text or "").strip()
    tr = (b.translation or "").strip()
    if not text or not tr or tr == text:
        return False
    x, y, w, h = (int(v) for v in b.bbox)
    return w >= 2 and h >= 2


def _region_dict(b: TextBlock) -> dict:
    """A recovered TextBlock as an engine-shaped region, tagged as this pass's work."""
    x, y, w, h = (int(v) for v in b.bbox)
    return {"x0": x, "y0": y, "x1": x + w, "y1": y + h,
            "text": (b.text or "").strip(), "translation": (b.translation or "").strip(),
            "angle": float(b.angle or 0.0), "prob": b.confidence, "pass": "second"}


def _ours(image_path: str, lang: str) -> list[TextBlock]:
    """Our own detect + OCR for one page, as untranslated TextBlocks."""
    if lang == "ja":
        from .pipeline import process_page

        return list(process_page(image_path))

    from .ocr_multilingual import read_boxes_text
    from .render import _ocr_ko_zh_native

    with Image.open(image_path) as im:
        page = im.convert("RGB")
    boxes = _ocr_ko_zh_native(page, np.asarray(page), None, "", lang, ocr_fn=read_boxes_text)
    out: list[TextBlock] = []
    for (x, y, w, h), text, conf, angle in boxes:
        if not text or not text.strip():
            continue
        out.append(TextBlock(bbox=(int(x), int(y), int(w), int(h)), text=text,
                             confidence=conf, orientation=_orientation(w, h),
                             angle=angle or 0.0))
    return out


def augment_regions(image_path: str, lang: str, engine_regions: list[dict],
                    model: str = "", api_key: str = "", base_url: str = "",
                    dry_run: bool = False, timeout: float | None = None,
                    max_retries: int | None = None,
                    min_free_gb: float = MEMORY_MIN_GB, log=None
                    ) -> tuple[list[dict], set[int], str]:
    """Find text the engine missed. Returns (regions_to_add, engine_indexes_to_drop, why).

    `engine_indexes_to_drop` is always empty now — the engine's regions are never touched —
    and stays in the signature because the caller's contract is "this pass can only ever add".
    `why` is always worth logging: "0 missed" and "did not run" look identical in the output
    otherwise.
    """
    say = log or (lambda *_a, **_k: None)
    if lang not in ("ja", "ko", "zh"):
        return [], set(), "skipped: unknown language"
    free = mem_available_gb()
    if free is not None and free < min_free_gb:
        return [], set(), "skipped: only %.1f GB free (needs %.1f GB)" % (free, min_free_gb)

    try:
        ours = _ours(image_path, lang)
    except Exception as e:  # noqa: BLE001
        return [], set(), "our detector failed: %s: %s" % (type(e).__name__, e)
    if not ours:
        return [], set(), "our detector found nothing"

    # TWO cases, and the split is the whole job:
    #   1. our box touches an engine region -> COVERED. The engine owns that text: it read it,
    #      it translated it, and our read of the same area is the degraded one (that is why
    #      upstream stopped where it did). Dropping or re-translating it here is how this pass
    #      lost a plate's English (v0.30.1), duplicated a plate's text (v0.30.2) and lettered
    #      romanised garbage (v0.30.5). Nothing happens.
    #   2. our box touches nothing -> a genuine standalone miss, and the only thing this pass
    #      adds.
    covered = 0
    standalone: list[TextBlock] = []
    for b in ours:
        box = _box_region(b)
        if any(_inter(box, _region_box(r)) > 0 for r in engine_regions):
            covered += 1
        else:
            standalone.append(b)

    if not standalone:
        return [], set(), ("0 missed of %d (all covered by the engine's %d regions)"
                           % (len(ours), len(engine_regions)))
    if dry_run:
        return [], set(), ("%d missed but dry_run (not translated)" % len(standalone))

    try:
        from .translate import translate_page

        translate_page(standalone, model, api_key, base_url, translate_horizontal=True,
                       source_lang=lang, timeout=timeout, max_retries=max_retries)
    except Exception as e:  # noqa: BLE001
        return [], set(), "translation of the misses failed: %s: %s" % (type(e).__name__, e)

    out = [_region_dict(b) for b in standalone if _letterable(b)]
    say("second pass: %d of our %d boxes covered by the engine, %d standalone misses, "
        "%d letterable" % (covered, len(ours), len(standalone), len(out)))
    return out, set(), ("%d covered by the engine, %d standalone miss(es), %d letterable"
                        % (covered, len(standalone), len(out)))
