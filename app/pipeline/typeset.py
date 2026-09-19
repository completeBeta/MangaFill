"""Typeset — render English translations back into text boxes.

English manga lettering is horizontal (LTR), bold, and centered in the balloon.
The hard part is fitting: pick the largest font size whose wrapped lines still
fit the box, then center the block.

Font selection lives in `app.pipeline.fonts`: the user-selected face (or the
Anime Ace default) is used when present, then the bundled OFL faces, then
DejaVu Sans Bold. See `resolve_font_path` there.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import fitlog
from .fonts import resolve_font_path
from .types import TextBlock

# Result of the most recent `_fit_common` call, so `_draw_box` can log what the fitter
# decided and why. The app is a single uvicorn worker and fits are serial, so a
# module-level record is safe; it is only filled when `fitlog.enabled()`.
_last_fit: dict = {}


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """Text width including the white-outline stroke (stroke extends outward)."""
    sw = max(1, font.size // 8)
    bb = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
    return int(bb[2] - bb[0])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Word-wrap to `max_w` via a minimum-raggedness dynamic program.

    The old greedy fill-left + balance pass left a short ragged last line, and
    in narrow bubbles broke one word per line ("I / WAITED / IN LINE / …").
    This finds the break sequence that minimises total squared slack (lines come
    out close to `max_w` and roughly even) with a small penalty on a lone
    trailing word so it pairs up with the line above when it fits. O(n²) in the
    word count — trivial for a speech bubble.

    Line widths come from each WORD's own extent plus the inter-word space: a
    joined string costs ~1ms to measure, and this loop measures every candidate
    line for every font size `_fit_common` tries, so a 47-word caption spent 31s
    per fit building and measuring joined strings (job-3 page 5). The sum is
    additive, so the DP gets the same numbers for O(1) arithmetic per candidate.
    """
    words = text.split()
    if not words:
        return [""]
    n = len(words)
    if n == 1:
        return words

    # Width of every candidate line words[i:j] in O(1): a word's own extent plus
    # the inter-word space is additive (the font also kerns across a space, by up
    # to a pixel — measured once per word boundary so the DP sees the SAME numbers
    # as measuring the joined string, which is what makes this a pure speedup).
    ww = [_text_w(draw, w, font) for w in words]
    _one = _text_w(draw, "a", font)
    space = max(0, _text_w(draw, "a a", font) - 2 * _one)
    kern = [
        _text_w(draw, f"{words[k]} {words[k + 1]}", font) - (ww[k] + space + ww[k + 1])
        for k in range(n - 1)
    ]

    inf = float("inf")
    cost = [inf] * (n + 1)
    brk = [0] * (n + 1)
    cost[n] = 0.0
    for i in range(n - 1, -1, -1):
        width = ww[i]
        boundary = 0
        for j in range(i + 1, n + 1):
            if j > i + 1:
                width += space + ww[j - 1]
                boundary += kern[j - 2]
            single = (j - i == 1)
            if width + boundary > max_w and not single:
                break  # multi-word line too wide; a wider j only grows
            if width + boundary > max_w:
                # A lone word wider than the box is forced onto its own line —
                # heavy penalty so the DP avoids it, but it keeps every position
                # breakable (never an infinite reconstruction loop).
                bad = 2.0
            elif j == n:
                # Last line: penalise a lone trailing word so it joins the line
                # above when it fits (0.6 > the max per-line slack² of 1.0).
                bad = 0.6 if single else 0.0
            else:
                slack = (max_w - (width + boundary)) / max_w
                bad = slack * slack
            c = cost[j] + bad
            if c < cost[i]:
                cost[i] = c
                brk[i] = j

    lines: list[str] = []
    i = 0
    while i < n:
        j = brk[i]
        if j <= i:  # safety net — every position is breakable, so this won't fire
            j = i + 1
        lines.append(" ".join(words[i:j]))
        i = j
    return lines


def _fit(text: str, max_w: int, max_h: int, font_path: str, max_font: int = 32,
         ignore_wordlist: bool = False):
    """Best font size for `text` in the rectangle (max_w x max_h) — see `_fit_common`."""
    return _fit_common(text, max_w, max_h, font_path, max_font,
                       ignore_wordlist=ignore_wordlist)

def _fit_common(text: str, max_w: int, max_h: int, font_path: str, max_font: int = 32,
                avail=None, region_h: int | None = None, ignore_wordlist: bool = False):
    """Best font size for `text` in a region — SIZE FIRST, taste second.

    v0.27.27. The objective is now a single ordered rule:

      1. only sizes that FIT (the rectangle, and — with `avail` — the balloon's
         outline row by row) are candidates;
      2. `largest_fitting` is the biggest of those. The chosen size may be at most
         **ONE step below it** (`floor`); nothing can make the lettering smaller;
      3. inside that band, score by covered area (fill), then by fewest one-word
         lines, then by size.

    Why this replaces the previous rule: that one had a HARD exclusion for the
    "word list" look (3+ lines, nearly all single words). Bigger letters wrap to
    fewer words per line, so the exclusion deleted precisely the large candidates —
    and the fill-maximisation then ran only over the small ones. Measured across a
    12-page sample: **20% of blocks** were under-sized by this alone, mean **+31%**
    (worst +100%): "That makes three." lettered at 9px where 18px fitted, and a
    148x234 balloon holding 10px text. Avoiding a stacked look is worth at most one
    size step, not a third of the font.

    `ignore_wordlist` is retained for callers/tests but is now a no-op: the
    word-list look is a tie-break inside the band, never a veto.
    """
    if max_w < 8 or max_h < 8:
        return None
    _tracing = fitlog.enabled()
    trace: list[dict] | None = [] if _tracing else None
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cands: list[tuple[float, int, int, tuple]] = []  # (fill, lone, size, (lines, font))
    largest_fitting: int | None = None
    for size in range(max_font, 7, -1):
        font = ImageFont.truetype(font_path, size)
        sw = max(1, size // 8)
        lines = _wrap(probe, text, font, max_w)
        joined = "\n".join(lines)
        bb = probe.multiline_textbbox(
            (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
        )
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        n = len(lines)
        lone = sum(1 for ln in lines if len(ln.split()) == 1)
        fill = (tw * th) / float(max(1, max_w * max_h))
        # Geometry only. Everything that fits becomes a candidate — no taste veto.
        if tw > max_w or th > max_h:
            reject = f"too_wide({int(tw)}>{max_w})" if tw > max_w else f"too_tall({int(th)}>{max_h})"
        elif avail is not None and not _lines_fit_shape(
            probe, lines, font, avail, region_h or max_h, th
        ):
            reject = "outside_shape"
        else:
            reject = None
            if largest_fitting is None:
                largest_fitting = size  # walked largest-first, so this is the max
            cands.append((fill, lone, size, (lines, font)))
        if trace is not None:
            trace.append({"size": size, "n": n, "lone": lone, "tw": int(tw),
                          "th": int(th), "fill": round(fill, 3), "reject": reject})
    _last_fit.clear()
    _last_fit.update({"table": trace, "largest_fitting": largest_fitting,
                      "max_w": int(max_w), "max_h": int(max_h), "text": text,
                      "avail_used": avail is not None, "chosen": None,
                      "avail_max": int(avail.max()) if avail is not None else None,
                      "avail_rows_usable": int((avail > 0).sum()) if avail is not None else None})
    if not cands or largest_fitting is None:
        return None
    # THE SIZE GUARANTEE: at most one step below the largest size that fits.
    floor = max(8, largest_fitting - 1)
    band = [c for c in cands if c[2] >= floor] or cands
    top = max(c[0] for c in band)
    keep = [c for c in band if c[0] >= 0.98 * top] or band
    keep.sort(key=lambda c: (c[1], -c[2]))
    fill, lone, size, (lines, font) = keep[0]
    _last_fit["chosen"] = size
    return (size, lines, font)


def _region_avail(gray: "np.ndarray", region: tuple, margin_frac: float = 0.04,
                  light: int = 200, min_fill: float = 0.30):
    """Lettering space inside the region's own light shape: (avail, box) or None.

    Speech balloons are ovals/spiked blobs, so their bounding box is NOT the space
    the lettering may use: text fitted to the bbox pokes out of the outline (the
    oval is much narrower at the top/bottom rows than at its waist). This returns

      * `box`  — the balloon interior's own bounding box (image coords), and
      * `avail`— for every row of that box, how wide a centred line may be before
                 it crosses the balloon's edge: `2 * min(run left, run right)`
                 around the interior's centre column, minus a margin that scales
                 with the region, so lettering never touches the outline.

    The interior is the connected light(>200) region containing the region's
    centre — the page handed here is already inpainted, so it is clean.

    Returns None when there is no such shape (text over artwork, caption strips,
    boxes that are all ink); callers then fall back to the rectangular fit.
    """
    x, y, w, h = [int(v) for v in region]
    if not hasattr(gray, "shape"):  # accept a PIL image too
        gray = np.asarray(gray)
    gx0, gy0 = max(0, x), max(0, y)
    gx1, gy1 = min(gray.shape[1], x + w), min(gray.shape[0], y + h)
    if gx1 - gx0 < 8 or gy1 - gy0 < 8:
        return None
    crop = gray[gy0:gy1, gx0:gx1] >= light
    ch, cw = crop.shape
    cx0, cy0 = cw // 2, ch // 2
    if not crop[cy0, cx0]:
        return None

    # Flood-fill the interior through light pixels (vectorised, no scipy: the
    # worker container has no scipy).
    visited = np.zeros_like(crop)
    visited[cy0, cx0] = True
    frontier = visited.copy()
    while frontier.any():
        grow = np.zeros_like(visited)
        grow[1:, :] |= frontier[:-1, :]
        grow[:-1, :] |= frontier[1:, :]
        grow[:, 1:] |= frontier[:, :-1]
        grow[:, :-1] |= frontier[:, 1:]
        frontier = grow & crop & ~visited
        visited |= frontier
    if visited.sum() < min_fill * crop.size:
        return None

    # Trim to the interior's own bbox: the lettering is centred in the BALLOON, not
    # in the (possibly looser) region box, and the width profile is then measured
    # symmetrically about the balloon's own centre column.
    ys, xs = np.where(visited)
    by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    sub = visited[by0:by1 + 1, bx0:bx1 + 1]
    bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
    cx = bw // 2
    # Runs from the centre column outwards, per row (cumprod = run length).
    left = np.cumprod(sub[:, :cx + 1][:, ::-1], axis=1).sum(axis=1)
    right = np.cumprod(sub[:, cx:], axis=1).sum(axis=1)
    avail = 2 * np.minimum(left, right) - 2
    margin = max(2, int(min(w, h) * margin_frac))
    avail = np.maximum(avail - 2 * margin, 0).astype(np.int32)
    box = (gx0 + bx0, gy0 + by0, bw, bh)
    return avail, box


def _lines_fit_shape(probe, lines: list[str], font, avail, region_h: int, th: int) -> bool:
    """True if every wrapped line fits the balloon's width at its own row band."""
    n = len(lines)
    if n == 0:
        return False
    top = max(0, (region_h - th) // 2)
    band = th / n
    for i, ln in enumerate(lines):
        y0 = int(top + i * band)
        y1 = min(region_h, max(y0 + 1, int(top + (i + 1) * band)))
        allowed = int(avail[y0:y1].min()) if y1 > y0 else 0
        if _text_w(probe, ln, font) > allowed:
            return False
    return True


def _fit_shape(text: str, avail, region_h: int, font_path: str, max_font: int = 32,
               ignore_wordlist: bool = False):
    """Largest font size whose wrapped lines all stay inside the balloon's shape.

    This is what lets lettering FILL a balloon without escaping it: the target is
    the shape, not the bounding box, so a big round balloon can be lettered much
    larger than a rectangular inset would allow, while a spiky or oval one is
    still never overrun. Scoring (fill, then fewest one-word lines) comes from
    `_fit_common` so the shape path fills as aggressively as the rectangular one.
    """
    max_w = int(avail.max())
    if max_w < 12 or region_h < 8:
        return None
    return _fit_common(text, max_w, region_h, font_path, max_font,
                       avail=avail, region_h=region_h,
                       ignore_wordlist=ignore_wordlist)


def _draw_box(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    bbox: tuple,
    text: str,
    font_path: str | None,
    max_font: int,
    angle: float = 0.0,
    avail=None,
):
    if not text or not text.strip():
        return
    if not font_path:
        return
    x, y, w, h = bbox
    # Fit text into the bubble's INSCRIBED region, not its full bounding box.
    # Speech bubbles are rounded-rect / oval, so their corners and top/bottom
    # edges curve away from the bbox; lettering sized to the full bbox crowds
    # the border (text touching the bottom edge, cramped top/bottom). An inset
    # that scales with the bubble's smaller dimension tracks the corner radius,
    # with a 6px floor so tiny bubbles keep the old breathing room. The block is
    # still centered via anchor="mm" below, so it stays centred inside the curve.
    inset = max(int(min(w, h) * 0.10), 6)
    iw = max(w - 2 * inset, 1)
    ih = max(h - 2 * inset, 1)

    # A tilted speech bubble needs tilted lettering. The fit region is the
    # largest rectangle (in the TEXT's rotated frame) whose θ-rotation stays
    # inside (iw, ih); fitting to that keeps the rotated text from spilling past
    # the bubble outline. Angles under ~3° are treated as level (detection jitter).
    if abs(angle) >= 3.0:
        th = math.radians(abs(angle))
        c, s = math.cos(th), math.sin(th)
        denom = c * c - s * s
        fw = (iw * c - ih * s) / denom if abs(denom) > 0.02 else 0.0
        fh = (ih * c - iw * s) / denom if abs(denom) > 0.02 else 0.0
        if fw > 8 and fh > 8:
            max_w, max_h = int(fw), int(fh)
        else:  # near-45° or box shape inconsistent with the angle
            side = max(int(min(iw, ih) / math.sqrt(2)), 1)
            max_w = max_h = side
    else:
        max_w, max_h = iw, ih

    # Dynamic per-box sizing: the largest size (capped at `max_font`) that fits
    # the box, measured by how much of the box the lettering covers (`_fit_common`).
    # Short text fills a big bubble; long text shrinks to fit a small one. If
    # nothing fits, fall back to 8px (may overflow) rather than blank.
    #
    # When the caller knows the box's actual shape (`avail`, from `_region_avail`)
    # the fit targets that shape instead of the rectangle: lettering fills an oval
    # balloon to its curve without ever crossing the outline. Rectangles remain
    # the fallback for slanted boxes and for regions with no enclosing shape.
    fitted = None
    if avail is not None and abs(angle) < 3.0:
        fitted = _fit_shape(text, avail, h, font_path, max_font=max_font)
        # v0.27.15 safety net: the outline fit can come out SMALLER than the
        # balloon's inscribed rectangle when the outline detection is fragmented —
        # `_region_avail` flood-fills the balloon interior, and lettering or art
        # inside the balloon breaks the light region into slivers, so `avail` reads
        # 0 on many rows and every size above the fragment size is rejected (job-1
        # page 7's 269x475 balloon lettered at 25px, 9% fill). Never letter smaller
        # than the inscribed rectangle (a 15% inset, the pre-v0.27.8 behaviour)
        # allows, so this can only ever grow the lettering, never shrink it.
        safe = max(int(min(w, h) * 0.15), 6)
        rect_w = max(w - 2 * safe, 1)
        rect_h = max(h - 2 * safe, 1)
        # Only reach for the rectangle when the shape profile is FRAGMENTED — the
        # case this net actually exists for (lettering/art inside the balloon splits
        # the light region, so `avail` reads 0 on many rows and the outline fit comes
        # out far smaller than the balloon allows). When `avail` reaches most of the
        # box width the outline fit is trustworthy, and forcing the rectangle there
        # lets the lettering cross a curved outline: job-3 p12's big ovals spilled
        # once the region became the real balloon instead of the detector's tight box.
        if avail.max() < 0.6 * rect_w:
            rect = _fit(text, rect_w, rect_h, font_path, max_font=max_font)
            if rect is not None and (fitted is None or rect[0] > fitted[0]):
                fitted = rect
    if fitted is None:
        fitted = _fit(text, max_w, max_h, font_path, max_font=max_font)
    if fitted is None:
        font = ImageFont.truetype(font_path, 8)
        lines = _wrap(draw, text, font, max_w)
    else:
        _size, lines, font = fitted

    joined = "\n".join(lines)
    # Phase 0 instrumentation (off unless the container was opted in): record what
    # this block got vs the largest size that would have fitted, plus the full size
    # table for blocks that under-sized. See `app/pipeline/fitlog.py`.
    if fitlog.enabled():
        _chosen = int(font.size) if fitted is not None else 8
        fitlog.record_block(bbox, text, bbox,
                            _last_fit.get("avail_max"),
                            _last_fit.get("avail_rows_usable"),
                            _last_fit.get("max_w", max_w),
                            _last_fit.get("max_h", max_h),
                            _chosen, _last_fit.get("largest_fitting"),
                            bool(_last_fit.get("avail_used")), len(lines))
        fitlog.record_candidates(_last_fit.get("table") or [], _chosen,
                                 _last_fit.get("largest_fitting"), text,
                                 bool(_last_fit.get("avail_used")))
    sw = max(1, font.size // 8)

    if abs(angle) < 3.0:
        draw.multiline_text(
            (x + w // 2, y + h // 2),
            joined,
            font=font,
            fill=(0, 0, 0),
            anchor="mm",
            align="center",
            spacing=2,
            # White outline behind the glyphs so black lettering stays readable over
            # dark boxes/screentone (stat tables, narration panels). The stroke is
            # sized to the font so it scales cleanly from 8px to the 32px cap.
            stroke_width=sw,
            stroke_fill=(255, 255, 255),
        )
        return

    # Render the text on a tight transparent layer, rotate it to the bubble's
    # slant, and paste it centred. PIL `rotate()` is counter-clockwise for
    # positive angles, so a positive (down-to-right) slant needs `-angle`.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bb = probe.multiline_textbbox(
        (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
    )
    tw = int(bb[2] - bb[0])
    th = int(bb[3] - bb[1])
    pad = sw + 6  # room for the stroke + bicubic resample edge
    layer = Image.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.multiline_text(
        (pad - bb[0], pad - bb[1]),
        joined,
        font=font,
        fill=(0, 0, 0),
        align="center",
        spacing=2,
        stroke_width=sw,
        stroke_fill=(255, 255, 255),
    )
    rotated = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
    px = int(x + w // 2 - rotated.width // 2)
    py = int(y + h // 2 - rotated.height // 2)
    image.paste(rotated, (px, py), rotated)


def typeset_page(
    image: Image.Image,
    blocks: list[TextBlock],
    font_path: str | None = None,
    font_id: str | None = None,
    regions: dict | None = None,
    only: set | None = None,
    shapes: set | None = None,
    page_label: str = "?",
) -> Image.Image:
    """Draw every translatable block's English translation into a copy of `image`.

    Furigana (ruby) and untranslated blocks (titles/SFX/watermarks) are skipped:
    furigana is erased, not re-lettered; SFX/titles stay as-is.

    `regions` optionally maps id(block) -> (x, y, w, h) container (the bubble/box
    interior) to draw into. When omitted, each block's own bbox is used.

    `shapes` optionally names the blocks whose `region` is a speech balloon (or a
    flood-filled speech box) rather than a bare rectangle. For those, the lettering
    is fitted to the balloon's actual outline (`_region_avail` / `_fit_shape`) so it
    fills the balloon without crossing the outline. Blocks not listed keep the
    rectangular fit.

    `only` optionally restricts drawing to a set of block ids (e.g. blocks the
    caller decided to typeset, excluding free-floating text left untouched).
    """
    out = image.copy()
    draw = ImageDraw.Draw(out)
    fitlog.set_page_label(page_label)
    fp = font_path or resolve_font_path(font_id)
    # Dynamic per-box lettering: each block is sized to its own box — the largest
    # font (capped at ~1/32 of page width) that fits, so a short line fills a big
    # bubble and long dialogue shrinks to fit a small one.
    cap = max(20, image.width // 32)
    gray = None
    for b in blocks:
        if only is not None and id(b) not in only:
            continue
        if b.orientation == "furigana":
            continue
        if not b.translation:
            continue
        region = regions.get(id(b), b.bbox) if regions else b.bbox
        text = b.translation
        if getattr(b, "is_sfx", False):
            # A drawn sound effect is art, not prose: letter it to occupy the space
            # the sound effect did. The page-width cap (`cap`, ~width/32 = 21px on
            # a 690px webtoon page) rendered a 421px-tall SFX as a 21px word, so
            # size the cap from the box instead. Uppercase, as comics letter SFX.
            bcap = min(200, max(14, int(min(region[2], region[3]))))
            text = text.upper()
        else:
            bcap = cap
            if b.orientation == "horizontal":
                # Horizontal captions/stat lines letter at a size tied to their own
                # text height, not the full-page cap — a short footnote should not
                # blow up to the max size just because the caption region is wide.
                bcap = min(cap, max(14, int(b.bbox[3] * 0.8)))
        avail = None
        if shapes and id(b) in shapes and abs(b.angle) < 3.0:
            if gray is None:
                gray = np.asarray(out.convert("L"))
            shape = _region_avail(gray, region)
            if shape is not None:
                avail, ebox = shape
                # letter into the balloon's OWN box: it is centred on the balloon,
                # not on the (possibly looser) detection box
                region = ebox
        _draw_box(out, draw, region, text, fp, bcap, angle=b.angle, avail=avail)
    fitlog.end_page()
    return out
