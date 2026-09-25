"""A block's OWN balloon interior, measured on the original page in a LOCAL window.

WHY THIS EXISTS (measured, not assumed)
---------------------------------------
The lettering regions in `render.py` are resolved page-wide: the detector's
`bubble` blob, a white flood (`bubble.find_container`), or a gap-tolerant flood.
When two balloons touch — or when our own erase removes an outline inside the
block's box — that page-wide resolution returns ONE interior belonging to two
balloons, and the fitter then letters the English across both outlines.

Whole-library measurement of the v0.27.41 render (job 3, 2,606 lettered blocks):
591 regions were shared by more than one block, covering 1,329 blocks; the audit
flagged 1,199 blocks (48.8%) as `merged` on 176 pages — by far the largest
remaining defect class, and untouched by anything shipped so far.

THE FIX SHAPE
-------------
Upstream manga-image-translator takes the opposite approach: it never resolves
regions page-wide. For each text block it takes a LOCAL window around that
block's own box, finds the balloon outline inside the window (Canny + contours +
a flood from the window centre that the outline blocks) and keeps the tightest
enclosed interior. Two touching balloons therefore come apart, because each
block's window only has to answer a local question.

Measured on this app's own stored pages before porting (job 3, ALL 591 merged
region groups / 1,329 blocks, using the vendored GPL-3.0 extractor at a 1.5x
window):
  * the extracted mask covers the block's OWN text box   mean 0.969, median 0.986
  * it covers a SIBLING block's text box                 mean 0.030, median 0.000
  * sibling pairs cleanly separated (<10% overlap)       89.6%
  * sibling pairs still merged (>50% overlap)             0.1%  (1 block)
Our current shared region covers every sibling's box by construction, i.e. 100%
"merged". The window enlargement matters: at 2.5x, 5.3% of blocks were back to
merged; at 4.0x, 27.8% were — a window that reaches the neighbour re-merges them,
so ENLARGE is deliberately small.

WHAT THIS MODULE IS NOT
-----------------------
It is not a replacement for the caption/free-text paths. Text drawn over artwork
with no outline has no enclosed interior to find, and class E ("narration may
widen across art") is deliberate behaviour: the extractor returns None there and
the caller keeps its existing region. Nothing is ever *widened* by this module —
it can only hand back a tighter region than the caller already had.

LICENCE: the extraction itself is GPL-3.0 vendored code (see
`app/vendor/manga_image_translator/` and /THIRD_PARTY.md). This wrapper is part of
Manga Fill and is covered by the project licence.
"""
from __future__ import annotations

import numpy as np

from ..vendor.manga_image_translator import ballon_extractor as _bx

# Validated window enlargement (see the module docstring: larger windows re-merge
# neighbouring balloons). Measured on ALL 591 merged region groups / 1,329 blocks of the
# v0.27.41 job-3 render: at 1.5x, sibling-box coverage was 3.0% (89.6% of blocks fully
# separated, 0.1% still merged, 3.0% refusals); at 2.5x, 15% covered; at 4.0x, 31%.
ENLARGE = 1.5

# Upstream's own window rule (text_render_eng.py:380-416): the window grows with the
# box's aspect ratio, up to 3x — and is then SHRUNK for any region whose enlarged box
# would intersect a neighbour's, so two adjacent balloons never share a window:
#   ratio = min(max(w/h, h/w) * 1.5, 3);  for a touching neighbour: ratio = d/(2*l) + 1
# Measured here on the same data, adaptive alone separates 100% of sibling pairs but
# refuses 16.4% of blocks (window shrunk past the outline ⇒ no balloon found ⇒ the
# caller keeps its old region, i.e. no gain but no regression). ADAPTIVE_FALLBACK
# therefore retries the refused ones at ENLARGE, and the sibling gate in the caller
# rejects anything that still swallows a neighbour.
ADAPTIVE = True
ADAPTIVE_FALLBACK = True

# ---------------------------------------------------------------------------
# BEHAVIOUR SWITCH — OFF BY DEFAULT (measured 2026-09-23, all three languages).
#
# With the switch ON, the whole-job audit of the v0.28.0 staging render gave, on matched
# blocks: ja job3 spill ink 393,149 -> 380,664 px (-3.2%, improved 491/worse 419, and
# -16.6% on the blocks whose interiors share a white component with a sibling);
# ko job2 123,473 -> 123,228 (-0.2%, 21/19); zh job1 140,964 -> 141,589 (+0.4% WORSE,
# 23/25). `narrow` got worse in all three (+21 / +1 / +1). The new path does fire in the
# real pipeline (386 `local-balloon` region records in the fitlog for that render).
#
# So the port is mechanically sound but NOT a demonstrated improvement: the v0.27.41
# sibling clip already stops the lettering crossing into a neighbour, and replacing that
# clip with the balloon's true outline mainly makes the usable width TIGHTER, which costs
# type size (`narrow`) without paying for it in outside-ink.
#
# Kept behind a switch so the next iteration can be A/B'd in one line. The proposed
# tightening: use the local balloon to CLIP the profile (min per row, like the existing
# `balloon_from_original` clip) instead of replacing region+mask wholesale, so separation
# is kept and the width is never reduced below what the current path grants.
ENABLED = False

# Gate values, chosen from the whole-library measurement above: masks that fail any
# of these are not trustworthy, and the caller keeps the region it already had.
MIN_OWN_COVER = 0.80      # the mask must cover most of the block's own text box
MIN_MASK_PX = 400         # below this the extraction found nothing meaningful
MAX_PAGE_FRAC = 0.60      # a mask this large is not a balloon, it is the page


def _rect_mask(shape: tuple, box) -> np.ndarray:
    m = np.zeros(shape, bool)
    x, y, w, h = (int(v) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(shape[1], x + w), min(shape[0], y + h)
    if x1 > x0 and y1 > y0:
        m[y0:y1, x0:x1] = True
    return m


def to_bgr(page: np.ndarray) -> np.ndarray:
    """The vendored extractor was measured against BGR page images — keep that
    exact input so the numbers in the module docstring describe the shipped code."""
    a = np.asarray(page)
    if a.ndim == 2:
        return np.repeat(a[:, :, None], 3, axis=2)
    if a.shape[2] == 4:
        a = a[:, :, :3]
    return np.ascontiguousarray(a[:, :, ::-1])


def rect_distance(a, b) -> float:
    """Gap between two `(x, y, w, h)` boxes in px; 0 when they touch or overlap.

    Mirrors upstream's `rect_distance` (utils/generic2.py) as used by the window
    pre-pass: the Euclidean gap between the two rectangles' edges.
    """
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    dx = max(0.0, max(ax0, bx0) - min(ax0 + aw, bx0 + bw))
    dy = max(0.0, max(ay0, by0) - min(ay0 + ah, by0 + bh))
    return float(np.hypot(dx, dy))


def adaptive_enlarge(block_box, sibling_boxes) -> float:
    """Upstream's window ratio for this block: aspect-based, shrunk near neighbours.

    `ratio = min(max(w/h, h/w) * 1.5, 3)`, then for every sibling whose ENLARGED box
    intersects ours, `ratio = min(ratio, d / (2 * l) + 1)` where `d` is the gap between
    the two source boxes and `l = (w + h) / 2` (the half-perimeter). The second step is
    upstream's mechanism for keeping two touching balloons from sharing a window
    (text_render_eng.py:393-416); the result is floored at 1.0, i.e. no enlargement.
    """
    x, y, w, h = (float(v) for v in block_box)
    if w <= 0 or h <= 0:
        return ENLARGE
    ratio = min(max(w / h, h / w) * 1.5, 3.0)
    ell = (w + h) / 2.0
    for sib in sibling_boxes or []:
        sx, sy, sw, sh = (float(v) for v in sib)
        if sw <= 0 or sh <= 0:
            continue
        # the sibling's own enlarged box at the same ratio
        s_ex, s_ey = sw * (ratio - 1) / 2.0, sh * (ratio - 1) / 2.0
        if not (x - w * (ratio - 1) / 2.0 < sx + sw + s_ex
                and sx - s_ex < x + w + w * (ratio - 1) / 2.0
                and y - h * (ratio - 1) / 2.0 < sy + sh + s_ey
                and sy - s_ey < y + h + h * (ratio - 1) / 2.0):
            continue
        ratio = min(ratio, rect_distance(block_box, sib) / (2.0 * ell) + 1.0)
    return max(1.0, ratio)


def local_balloon_region(page_bgr: np.ndarray, block_box, page_w: int, page_h: int,
                         *, enlarge: float = None, sibling_boxes=None,
                         min_own_cover: float = MIN_OWN_COVER,
                         min_mask_px: int = MIN_MASK_PX,
                         max_page_frac: float = MAX_PAGE_FRAC):
    """The balloon interior around `block_box`: `(mask, box)` or `None`.

    `mask` is a page-sized boolean array; `box` is its bounding box `(x, y, w, h)`
    in page coordinates. `None` means "not trustworthy — keep the region you had".
    """
    x, y, w, h = (int(v) for v in block_box)
    if w <= 0 or h <= 0:
        return None
    arr = np.asarray(page_bgr)
    if arr.ndim != 3 or arr.shape[2] != 3:
        return None
    # A window that is entirely off-page cannot be analysed.
    if x + w <= 0 or y + h <= 0 or x >= arr.shape[1] or y >= arr.shape[0]:
        return None

    def _attempt(ratio: float):
        if ratio <= 1.0:
            return None
        try:
            # Upstream's own annotation says this returns a 3-tuple; it returns
            # (mask, [x1, y1, x2, y2]) — vendored file, not ours to fix. A float ratio
            # is fine too (it is only compared against 1.0 there).
            _res = _bx.extract_ballon_region(arr, [x, y, w, h], ratio)  # type: ignore[misc]
            mask, rect = _res[0], _res[1]
        except Exception:
            return None
        if mask is None or not hasattr(mask, "shape") or mask.size == 0:
            return None
        local = (mask > 0).astype(bool)
        x1, y1, x2, y2 = (int(v) for v in rect)
        page_mask = np.zeros(arr.shape[:2], bool)
        sx0, sy0 = max(0, x1), max(0, y1)
        sx1, sy1 = min(arr.shape[1], x2), min(arr.shape[0], y2)
        if sx1 <= sx0 or sy1 <= sy0:
            return None
        lx0, ly0 = sx0 - x1, sy0 - y1
        sub = local[ly0:ly0 + (sy1 - sy0), lx0:lx0 + (sx1 - sx0)]
        page_mask[sy0:sy1, sx0:sx1] = sub

        px = int(page_mask.sum())
        if px < min_mask_px:
            return None
        if px > max_page_frac * float(page_w * page_h):
            return None
        own = _rect_mask(page_mask.shape, (x, y, w, h))
        own_px = int(own.sum())
        if own_px and (page_mask & own).sum() < min_own_cover * own_px:
            return None
        ys, xs = np.nonzero(page_mask)
        if ys.size == 0:
            return None
        bx0, bx1 = int(xs.min()), int(xs.max())
        by0, by1 = int(ys.min()), int(ys.max())
        return page_mask, (bx0, by0, bx1 - bx0 + 1, by1 - by0 + 1)

    # Window choice: the caller's value wins; otherwise upstream's adaptive rule when the
    # caller told us about the neighbours, else the measured fixed ratio.
    ratios: list[float] = []
    if enlarge is not None:
        ratios = [float(enlarge)]
    else:
        if ADAPTIVE and sibling_boxes:
            ratios.append(adaptive_enlarge(block_box, sibling_boxes))
        if not ratios or (ADAPTIVE_FALLBACK and ratios[0] != ENLARGE):
            ratios.append(ENLARGE)
    seen: set[float] = set()
    for ratio in ratios:
        if ratio in seen:
            continue
        seen.add(ratio)
        got = _attempt(ratio)
        if got is not None:
            return got
    return None


def own_cover(page_mask: np.ndarray, block_box) -> float:
    """Diagnostic: the share of a block's own text box the mask covers."""
    own = _rect_mask(page_mask.shape, block_box)
    n = int(own.sum())
    return float((page_mask & own).sum()) / n if n else 0.0
