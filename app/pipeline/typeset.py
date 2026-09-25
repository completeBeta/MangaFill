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
import re
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import fitlog
from .fonts import resolve_font_path, resolve_legibility_fallback
from .types import TextBlock

# The smallest lettering a reader can comfortably read at normal viewing size. Below it
# the block gets a NARROWER face rather than staying microscopic (`fonts.
# resolve_legibility_fallback`) — the fitter's own search runs down to 8px, which is
# legible only in isolation, not on a page beside 22px dialogue.
# Measured basis (2026-09-22, job-3 pp. 4-164): 30 blocks under 14px; the configured
# face reached 14px on none of them, a narrower bundled face on 21.
_LEGIBLE_FLOOR = 14

# Result of the most recent `_fit_common` call, so `_draw_box` can log what the fitter
# decided and why. The app is a single uvicorn worker and fits are serial, so a
# module-level record is safe; it is only filled when `fitlog.enabled()`.
_last_fit: dict = {}


def _log(level: str, msg: str, *args) -> None:
    """Log through the app's own logger. Instrumentation must never break a render."""
    try:
        from app.services.logging import get_logger

        log = get_logger("typeset")
        if log is not None:
            getattr(log, level, log.info)(msg, *args)
    except Exception:
        pass

# How far `_region_avail` moved the lettering box to land on the balloon's own axis
# (see there). Set on every successful call and CONSUMED by `_draw_box`, so a block
# that never used the shape path cannot report a stale shift.
_last_shape: dict = {}

# How close the lettering may come to the balloon outline, as a multiple of the
# strictly available width. 1.0 = never cross. The shape test used to demand every
# line fit the NARROWEST row of its own band, which on a tapered balloon rejects
# almost every size above a small one: measured on job-3 page 7, "This time he
# collapsed just from lightly running around the yard..." fitted at 12px inside a
# 226x274 balloon where the reference release uses 18px.
#
# 1.10 is MEASURED, not chosen for taste — the largest value that keeps the ink inside
# the outline on BOTH shape classes (container fonts, drawn with the real code path):
#
#   tol   spiky balloon (312x280)      oval (180x274)
#   1.00  29px/5ln, 0px outside        18px/10ln, 0px outside
#   1.10  31px/5ln, 0px outside        19px/10ln, 0px outside
#   1.15  33px/5ln, 122px OUTSIDE      19px/10ln, 0px outside
#   1.20  34px/5ln, 285px OUTSIDE      20px/8ln,  0px outside
#
# So a 20% tolerance buys one more size step on an oval and visibly spills out of a
# spiky balloon. Trade lettering does sit close to the outline — the commercial
# `manga2eng` renderer in `manga-image-translator` uses 1.2 — but its balloons are not
# this project's, and "text crossing the outline" is the one defect that must never
# ship. (That project is GPL-family: approach only, never code.)
SHAPE_TOL = 1.10

# Wrap widths to try, as fractions of the balloon's widest row. The old code
# wrapped ONLY to the widest row and then required every line to fit its band's
# narrowest row — the two rules fight, so the fit collapses. Wrapping NARROWER
# gives more, shorter lines that sit inside the oval's tapered top/bottom rows,
# which lets the same shape test admit a much bigger glyph. This is the
# "narrow column, more lines, larger letters" look the reference uses.
WRAP_FRACS = (1.0, 0.9, 0.8, 0.7, 0.6)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """Text width including the white-outline stroke (stroke extends outward)."""
    sw = max(1, font.size // 8)
    bb = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
    return int(bb[2] - bb[0])


_QUOTE_RUN = "\"'”’）」』】"
_EL_TAIL = re.compile(r"[.．…]{2,}$")


def _token_breaks(w: str) -> list[str]:
    """Typographically legal split points inside a single token ([] = none).

    Two, and only two, are honoured:
      * after an internal hyphen — `MIO-SAN!` -> `MIO-` + `SAN!`
      * before a trailing ellipsis run — `"normal"...` -> `"normal"` + `...`
    A bare word is NEVER cut into letter fragments (see `_break_long_words`).
    """
    parts = [p for p in re.split(r"(?<=-)", w) if p]
    if len(parts) > 1:
        return parts
    m = _EL_TAIL.search(w)
    if m and m.start() > 0:
        return [w[:m.start()], w[m.start():]]
    return []


def _break_long_words(draw: ImageDraw.ImageDraw, words: list[str], font,
                      max_w: int) -> list[str]:
    """Split TOKENS that cannot fit `max_w` on one line, so they can wrap.

    A single token wider than the line has no break point, so it is forced onto one line at
    whatever size fits the width — which is how job-3 p85's 70x140 balloon came back with
    `MIO-SAN!` lettered at 13px where the earlier, less accurate `MIO!` had filled it at
    28px. Comic lettering's own convention for a long word is to break it at a hyphen, so
    `MIO-SAN!` becomes `MIO-` + `SAN!` and the balloon can use a bigger size.

    v0.27.41 adds the second legal break: a TRAILING ELLIPSIS. `"normal"...` is one token
    because the quotes and the ellipsis ride along with the word, and on job-3 page 10 it
    was the ONLY thing capping that balloon's dialogue at 16px — the token measured 120px
    at 17px against a 116px profile, so every size above 16 was rejected and the balloon
    kept a small stacked column where the source filled it. Splitting at the ellipsis is a
    standard typographic break (the marks belong to the line they trail), it costs nothing
    visually, and it lets the band rule reach the size the balloon actually holds.

    ONLY those two break points are used, and only when the token cannot fit on its own line:
      * a word with no hyphen and no trailing ellipsis is left exactly as before (the
        fitter's "force one word onto a line, never loop" invariant, which
        `test_wrap_word_wider_than_box_does_not_hang` pins — an unbreakable token must still
        be placeable, not split into unreadable fragments);
      * a token that fits is untouched, so the wrap is byte-identical everywhere else.
    """
    out: list[str] = []
    for w in words:
        if _text_w(draw, w, font) <= max_w:
            out.append(w)
            continue
        parts = _token_breaks(w)
        if len(parts) < 2 or any(_text_w(draw, p, font) > max_w for p in parts):
            out.append(w)             # splitting would not help (or a piece still too wide)
            continue
        out.extend(parts)
    return out


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
    words = _break_long_words(draw, words, font, max_w)
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
    # Invalidate first: if this call bails out early (a degenerate region) the previous
    # block's trace must NOT be re-reported against this one. The audit reads
    # `stale`/`text` to decide whether a trace actually belongs to the block.
    _last_fit.clear()
    _last_fit["stale"] = True
    if max_w < 8 or max_h < 8:
        return None
    _tracing = fitlog.enabled()
    trace: list[dict] | None = [] if _tracing else None
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cands: list[tuple] = []          # (fill, lone, size, (lines, font), wrap, frac)
    largest_fitting: int | None = None
    # With a shape profile, try several wrap widths per size (see `WRAP_FRACS`) and
    # keep the widest-filling one. Without a profile there is nothing to adapt to,
    # so the rectangle path stays a single wrap at `max_w`.
    fracs = WRAP_FRACS if avail is not None else (1.0,)
    for size in range(max_font, 7, -1):
        font = ImageFont.truetype(font_path, size)
        sw = max(1, size // 8)
        best: tuple | None = None            # (fill, lone, size, (lines, font))
        best_at: tuple | None = None         # (wid, frac, n, tw, th, fill, reject)
        reject_any: str | None = None
        for frac in fracs:
            wid = max(12, int(max_w * frac))
            lines = _wrap(probe, text, font, wid)
            joined = "\n".join(lines)
            bb = probe.multiline_textbbox(
                (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
            )
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            n = len(lines)
            lone = sum(1 for ln in lines if len(ln.split()) == 1)
            fill = (tw * th) / float(max(1, max_w * max_h))
            reject = None
            # Geometry only. Everything that fits becomes a candidate — no taste veto.
            if tw > max_w or th > max_h:
                reject = f"too_wide({int(tw)}>{max_w})" if tw > max_w else f"too_tall({int(th)}>{max_h})"
            else:
                top = None
                if avail is not None:
                    # PLACEMENT-AWARE acceptance (see `_placement_top`). The block may sit
                    # anywhere in the region that keeps every line inside the balloon's
                    # own profile, so a tapered or tailed balloon costs a SLIDE, not a
                    # size step — the v0.27.34 failure was exactly that trade being made
                    # the other way round. Each line is validated at the rows it will
                    # actually occupy (`_line_metrics`), not at a uniform band.
                    _lw, _lrows = _line_metrics(probe, lines, font, stroke_width=sw)
                    top = _placement_top(_lrows, _lw, avail, region_h or max_h,
                                         tol=SHAPE_TOL, pad_rows=sw + 3)
                    if top is None:
                        reject = "outside_shape"
                if reject is None:
                    cand = (fill, lone, size, (lines, font), wid, frac, top)
                    if best is None or cand[0] > best[0]:
                        best = cand
                        best_at = (wid, frac, n, int(tw), int(th), round(fill, 3), None)
            if reject is not None and reject_any is None:
                reject_any = reject
        if best is not None:
            if largest_fitting is None:
                largest_fitting = size  # walked largest-first, so this is the max
            cands.append(best)
        if trace is not None:
            if best_at is not None:
                trace.append({"size": size, "n": best_at[2], "tw": best_at[3],
                              "th": best_at[4], "fill": best_at[5],
                              "wrap": best_at[0], "frac": best_at[1], "reject": None})
            else:
                trace.append({"size": size, "n": 0, "tw": 0, "th": 0, "fill": 0.0,
                              "wrap": 0, "frac": None, "reject": reject_any})
    _last_fit.clear()
    _last_fit.update({"stale": False, "table": trace, "largest_fitting": largest_fitting,
                      "max_w": int(max_w), "max_h": int(max_h), "text": text,
                      "avail_used": avail is not None, "chosen": None,
                      "avail_max": int(avail.max()) if avail is not None else None,
                      "avail_rows_usable": int((avail > 0).sum()) if avail is not None else None})
    if not cands or largest_fitting is None:
        return None
    # THE SIZE GUARANTEE: at most one step below the largest size that fits.
    floor = max(8, largest_fitting - 1)
    band = [c for c in cands if c[2] >= floor] or cands
    # SIZE FIRST (v0.27.31). Inside the band take the LARGEST size, not the
    # most-ink one: the whole point is lettering as big as the balloon allows, and
    # a fill-maximising rule prefers a tall stack of short lines (measured on the
    # modelled p7 balloon: it picked 15px over 10 lines where 16px over 8 lines
    # fitted, purely because the stack covers more area — the "word list" look).
    # `fill` still chooses the WRAP WIDTH at a given size (above), and is kept here
    # only to reject a size whose geometry collapsed pathologically. `lone` breaks
    # ties between equal sizes.
    top = max(c[0] for c in band)
    keep = [c for c in band if c[0] >= 0.70 * top] or band
    keep.sort(key=lambda c: (-c[2], c[1]))
    fill, lone, size, (lines, font), wrap_used, frac_used, top_used = keep[0]
    _last_fit["chosen"] = size
    # Which wrap width won — the whole point of the v0.27.31 change is that a
    # NARROWER column can carry a LARGER glyph, so the audit needs to see it.
    _last_fit["wrap"] = int(wrap_used) if wrap_used else None
    _last_fit["frac"] = float(frac_used) if frac_used is not None else None
    # Where the block goes inside its region (None = centred, the default). See
    # `_placement_top`: a tapered balloon costs a slide, never a size step.
    _last_fit["top"] = int(top_used) if top_used is not None else None
    return (size, lines, font)


def _flood(mask: "np.ndarray", seed: "np.ndarray") -> "np.ndarray":
    """The 4-connected components of `mask` that contain at least one `seed` pixel.

    Uses `cv2.connectedComponents` when OpenCV is importable (the app always has it —
    `bubble.py` needs it), because the vectorised numpy dilation this replaced is
    O(iterations x pixels) and `balloon_from_original` can be handed a whole-page crop
    whose flood then takes minutes. The numpy loop stays as the fallback so the module
    still works anywhere OpenCV is absent, and the two have the same semantics (4-neighbour).
    """
    if mask.size == 0 or not seed.any():
        return np.zeros_like(mask)
    try:
        import cv2  # noqa: PLC0415

        n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
        labels = np.unique(lab[seed])
        labels = labels[labels > 0]
        if labels.size == 0:
            return np.zeros_like(mask)
        lut = np.zeros(int(n), dtype=bool)
        lut[labels] = True
        return lut[lab]
    except Exception:
        pass
    visited = seed & mask
    while True:
        grow = np.zeros_like(visited)
        grow[1:, :] |= visited[:-1, :]
        grow[:-1, :] |= visited[1:, :]
        grow[:, 1:] |= visited[:, :-1]
        grow[:, :-1] |= visited[:, 1:]
        frontier = grow & mask & ~visited
        if not frontier.any():
            return visited
        visited |= frontier


_SEALED_MIN_FRAC = 0.02   # the interior is at least this share of the crop ...
_SEALED_MAX_FRAC = 0.95   # ... and never (nearly) all of it — then there is no outline
_SEALED_MIN_SPAN = 0.45   # a balloon fills its crop on BOTH axes; a glyph does not


def balloon_from_original(orig_gray, region: tuple, focus: tuple | None = None,
                          light: int = 200, pad_frac: float = 0.6, pad_min: int = 64,
                          tries: int = 3, min_area: float = 0.4, max_area: float = 25.0,
                          box: tuple | None = None):
    """The BALLOON around `focus`, measured on the ORIGINAL page: `(mask, box)` or None.

    `box` is the balloon's bounding box in page coordinates and `mask` is exactly that
    box — the balloon's INTERIOR (holes filled: the source glyphs and any art inside the
    balloon are part of the mask, so its per-row extents describe the full width the
    lettering may use). That is the same quantity `_region_avail` measures from a flood,
    only taken from the page whose outline is still there.

    WHY THE ORIGINAL. `region` (a container from `find_container`, or the detector's
    bubble box) is resolved on the ORIGINAL too, but the FITTER is handed the INPAINTED
    page — and our own erase masks run over the strokes inside the block's box, which
    includes a balloon's outline wherever the box clips it. Confirmed on job-3 p164: the
    erase took the oval's whole top cap, so the flood on the inpainted page walked out
    through the gap and measured the REGION BOX as free space (a flat 206x205 profile),
    the lettering was sized to it, and its first line was drawn across the arc. The
    container was also a 205px pocket inside a ~460px balloon (glyphs are walls to a
    flood on a page that still has its text), so the English sat in the balloon's top
    half at the wrong size. The ORIGINAL's enclosed interior is the balloon, whole.

    HOW. Light (`>= light`) components that the crop's border CANNOT reach are enclosed by
    dark outlines: those are balloon interiors. Their holes (the glyphs) are filled, so a
    densely lettered balloon does not come back as one pocket between two kanji columns —
    the trap the older measurement notes record. Working from the light side rather than
    "everything enclosed" is what keeps a balloon that sits on screentone or touches a
    panel rule from merging into it.

    The crop is padded and GROWN when the interior touches the crop edge (an outline cut
    by the crop is what lets a flood in). Refused (⇒ None, caller keeps its existing
    behaviour) when: no interior is enclosed near the text box, the interior does not
    reach `min_area` or exceeds `max_area` times the text box (noise, or a swallowed
    panel), or a page-sized crop still cuts it.
    """
    if orig_gray is None:
        return None
    if not hasattr(orig_gray, "shape"):
        orig_gray = np.asarray(orig_gray)
    if orig_gray.ndim == 3:
        orig_gray = orig_gray[..., 0]
    ph, pw = orig_gray.shape[:2]
    x, y, w, h = [int(v) for v in region]
    if w <= 0 or h <= 0:
        return None
    if focus is None:
        fx, fy = x + w // 2, y + h // 2
    else:
        fx, fy = int(focus[0]), int(focus[1])
    pad = max(pad_min, int(max(w, h) * pad_frac))
    box_area = float(max(1, w * h))
    for _ in range(max(1, tries)):
        cx0, cy0 = max(0, x - pad), max(0, y - pad)
        cx1, cy1 = min(pw, x + w + pad), min(ph, y + h + pad)
        if cx1 - cx0 < 16 or cy1 - cy0 < 16:
            return None
        crop = orig_gray[cy0:cy1, cx0:cx1] >= light
        ch, cw = crop.shape
        border = np.zeros_like(crop)
        border[0, :] = True
        border[-1, :] = True
        border[:, 0] = True
        border[:, -1] = True
        # Light the crop's border can reach = the page's background; what it cannot reach
        # is inside a closed dark outline.
        inner = crop & ~_flood(crop, border)
        filled = None
        if inner.any():
            # Seed from the enclosed light pixels INSIDE THE TEXT BOX — where the text is,
            # the balloon is (the box centre is usually a glyph, i.e. not light, so seeding
            # on the centre pixel alone would miss).
            bx0, by0 = max(0, x - cx0), max(0, y - cy0)
            bx1, by1 = min(cw, x + w - cx0), min(ch, y + h - cy0)
            if bx1 - bx0 >= 2 and by1 - by0 >= 2:
                seed = np.zeros_like(inner)
                seed[by0:by1, bx0:bx1] = inner[by0:by1, bx0:bx1]
                if seed.any():
                    interior = _flood(inner, seed)
                    # Fill the interior's holes (the source glyphs, art drawn inside the
                    # balloon) so the per-row extents describe the balloon's full width,
                    # not one pocket between two columns of text. Everything around the
                    # interior is reachable from the border through non-interior pixels, so
                    # this cannot leak into the screentone.
                    filled = ~_flood(~interior, border)
        # No enclosure found, or the enclosure runs off the crop: the crop CUT the
        # balloon's outline. That is what a region which is a pocket inside a big balloon
        # looks like (job-3 p164: a 205px container in a ~460px balloon), and it is not a
        # reason to give up — grow the crop and try again. (Returning early on the empty
        # seed here was a real bug: it silently disabled the fix for exactly the case the
        # fix exists for.)
        if filled is None or (filled[0, :].any() or filled[-1, :].any()
                              or filled[:, 0].any() or filled[:, -1].any()):
            if pad * 2 <= max(ph, pw):
                pad *= 2
                continue
            return None
        n = int(filled.sum())
        if n < min_area * box_area or n > max_area * box_area:
            return None
        ys, xs = np.where(filled)
        by0i, by1i, bx0i, bx1i = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
        bw, bh = bx1i - bx0i + 1, by1i - by0i + 1
        if bw < 12 or bh < 12:
            return None
        # TWO BALLOONS THAT TOUCH ARE ONE INTERIOR (job-3 p85, measured: a 156px block, a
        # 301px enclosure, two balloons, and the English drawn across the wall between
        # them). The block's OWN box is the tell: an enclosure far wider than the box the
        # text came from is a pair, not a balloon. Cut the necks and keep the block's own
        # balloon. (A block whose box genuinely spans both lobes keeps its full mask: the
        # guard below cannot fire, because its box is that wide too.)
        box_w = int(box[2]) if box is not None and len(box) >= 3 else max(1, w)
        box_h = int(box[3]) if box is not None and len(box) >= 4 else max(1, h)
        # Only a ONE-SIDED overhang is a union. A wide balloon whose source text is a narrow
        # column (very common: two columns of kanji in a broad oval) also exceeds 1.5x the
        # box on BOTH sides, and bounding THEM to the box shrank lettering that was never
        # crossing (measured: the first version of this guard touched 518/2746 blocks and
        # left 68 of them worse, against 260 improved). A neighbour balloon hangs off ONE
        # side: job-3 p85 is 114px past the box's right edge and only 31px past its left.
        _bx = int(box[0]) if box is not None and len(box) >= 3 else x
        _over_l = _bx - (cx0 + bx0i)
        _over_r = (cx0 + bx1i) - (_bx + box_w)
        _far, _near = max(_over_l, _over_r), min(_over_l, _over_r)
        if (bw > 1.5 * box_w and _far > 0.45 * box_w and _near < 0.5 * _far):
            # The enclosure is a UNION of this balloon and a neighbour, and it CANNOT be cut
            # apart: on job-3 p85 the distance transform has a single component at every
            # threshold up to 60px, i.e. there is no neck — the two balloons are broadly
            # joined (or the light connects through a gap in the crossing ink). Refusing is
            # no help either: the flood on the inpainted page is exactly as wide (274px), so
            # the lettering would still be fitted to the pair.
            #
            # So bound it by the only thing we DO trust: the block's OWN box — the extent the
            # ORIGINAL text occupied, which is inside the right balloon by construction. The
            # English may then be at most as wide as the source text's box, so it can never
            # be drawn further out than the original lettering was.
            # The window is an INTERSECTION with the enclosure, never a shape of its own: the
            # per-row extents stay the union's (narrow where the oval narrows) and only the
            # width is capped. A "cut" that grows an eroded core back outwards was tried and
            # REMOVED: it returns a lozenge with hard edges, i.e. a rectangular profile that
            # overstates the oval's narrow rows, so the fit sized UP and the placement thought
            # it fitted — measured on job-3 p45 (out 21 -> 527px) while the window path fixed
            # p85 correctly.
            if True:
                # no cut available: intersect with the box itself, grown just enough to let
                # the lettering use the balloon's own padding (12% a side)
                gx = int(0.12 * box_w)
                gy = int(0.12 * box_h)
                kx = int(box[0]) if box is not None else x
                ky = int(box[1]) if box is not None else y
                x0 = max(0, kx - cx0 - gx)
                x1 = min(cw, kx + box_w - cx0 + gx)
                y0 = max(0, ky - cy0 - gy)
                y1 = min(ch, ky + box_h - cy0 + gy)
                keep = np.zeros_like(filled)
                keep[y0:y1, x0:x1] = True
                inside = filled & keep
                # The bound must still be able to hold the block: a window that would leave
                # less area than the block's own box is not a balloon, it is a mistake, and
                # the enclosure is kept unchanged instead. (Keyed on the BOX, not on a share
                # of the enclosure: a 60x80 block inside a big oval is 8% of it, and a
                # threshold on that share silently disabled the bound for every small block —
                # it did, in this test.)
                if inside.sum() >= 0.6 * (box_w * box_h):
                    _log("UNION ENCLOSURE: %dpx wide for a %dpx block — bounded the lettering "
                         "to the block's own box (%dpx)", bw, box_w, x1 - x0)
                    filled = inside
                    ys2, xs2 = np.where(inside)
                    by0i, by1i = int(ys2.min()), int(ys2.max())
                    bx0i, bx1i = int(xs2.min()), int(xs2.max())
            n = int(filled.sum())
            bw, bh = bx1i - bx0i + 1, by1i - by0i + 1
        frac = n / float(bw * bh)
        if frac < _SEALED_MIN_FRAC or frac > _SEALED_MAX_FRAC:
            return None
        mask = filled[by0i:by1i + 1, bx0i:bx1i + 1]
        return mask, (cx0 + bx0i, cy0 + by0i, bw, bh)
    return None


def _profile_from_mask(mask: "np.ndarray", orig_crop: "np.ndarray" | None,
                       margin_frac: float = 0.04, outline: int = 4, dark: int = 150):
    """Lettering profile from a balloon mask: `(avail, (dx, dy))` or None.

    `avail[r]` is how wide a line may be on row `r` of `mask` — one entry per row, exactly
    like `_region_avail`, so `_lines_fit_shape` reads it unchanged. `dx`/`dy` are how far
    the mask's bounding box must move to sit on the balloon's axis (the v0.27.32 rule: a
    tail/bulge drags the bbox centre off the balloon, and a tail is many narrow rows, so
    weighting by row width barely moves the centre).

    The mask is the balloon's FOOTPRINT, outline included, so a row ENDS on the outline.
    Where it does, the extent is inset by `outline` px (measured on the original crop)
    so the lettering is fitted to the interior's wall rather than the outline's outer
    edge — this is what keeps a line inside the oval's top cap instead of across the arc.
    """
    if mask is None or not mask.any():
        return None
    bh, bw = mask.shape
    if bw < 12 or bh < 12:
        return None
    rows_any = mask.any(axis=1)
    if not rows_any.any():
        return None
    x_first = np.where(rows_any, mask.argmax(axis=1), 0)
    x_last = np.where(rows_any, bw - 1 - mask[:, ::-1].argmax(axis=1), 0)
    r_idx = np.arange(bh)
    if orig_crop is not None and getattr(orig_crop, "shape", None) is not None \
            and orig_crop.shape[:2] == (bh, bw):
        darkm = orig_crop < dark
        il = np.where(darkm[r_idx, np.clip(x_first, 0, bw - 1)], outline, 0)
        ir = np.where(darkm[r_idx, np.clip(x_last, 0, bw - 1)], outline, 0)
    else:
        il = ir = np.zeros(bh, dtype=np.int64)
    width = np.where(rows_any, x_last - x_first + 1 - il - ir, 0).astype(np.int64)
    margin = max(2, int(min(bw, bh) * margin_frac))
    avail = np.maximum(width - 2 * margin, 0).astype(np.int32)
    wsum = float(width.sum())
    if wsum <= 0:
        return None
    cents = (x_first + il + x_last - ir) / 2.0
    axis_x = float((cents * width).sum() / wsum)
    axis_y = float((r_idx * width).sum() / wsum)
    dx = axis_x - bw / 2.0
    dy = int(round(axis_y - bh / 2.0))
    # `avail` is indexed by MASK row, but the caller draws into the box, and the box sits
    # at mask_bbox + (dx, dy). Shift the profile by dy so box row r describes mask row
    # r - dy; rows that fall off the end carry 0 (no space) rather than a neighbour's
    # width, which would quietly allow a line to cross.
    if dy > 0:
        avail = (np.concatenate([np.zeros(min(dy, bh), np.int32), avail[:bh - dy]])
                 if dy < bh else np.zeros(bh, np.int32))
    elif dy < 0:
        k = min(-dy, bh)
        avail = np.concatenate([avail[k:], np.zeros(k, np.int32)])
    return avail, (dx, float(dy))


def _region_avail(gray: "np.ndarray", region: tuple, margin_frac: float = 0.04,
                  light: int = 200, min_fill: float = 0.30, orig_gray=None,
                  focus: tuple | None = None, box: tuple | None = None,
                  shape_mask=None):
    """Lettering space inside the region's own light shape: (avail, box) or None.

    Speech balloons are ovals/spiked blobs, so their bounding box is NOT the space
    the lettering may use: text fitted to the bbox pokes out of the outline (the
    oval is much narrower at the top/bottom rows than at its waist). This returns

      * `box`  — a box of the interior's own size, RE-CENTRED on the interior's
                 visual centre (see below), and
      * `avail`— for every row of that box, how wide a line may be before it
                 crosses the balloon's edge: the interior's own extent in that
                 row, minus a margin that scales with the region, so lettering
                 never touches the outline.

    The returned `box` is deliberately NOT the interior's bounding box: the bbox
    centre is skewed whenever the interior is asymmetric (a tail, a bulge, or an
    outline that runs into a panel border). Measured on job-3 page 7: the interior's
    bbox centred on x=451 while every row of the balloon centred on x=468-470, so
    lettering centred on the bbox sat ~18px left of the balloon — visibly off-centre
    against the reference, which centres on x=469.5. The centre used here is the
    WIDTH-WEIGHTED mean of the interior's per-row centres (an area centroid), so the
    slack is split evenly on both axes.

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
    # The caller may hand in the interior ALREADY MEASURED (vendored per-block balloon
    # extraction, `balloon_local`): that mask comes from the ORIGINAL page in a window
    # local to this one block, so it IS the interior — there is nothing to flood from
    # the inpainted page and nothing to clip. This is the two-balloon fix: the flood
    # below is page-wide, so two balloons whose outlines touch (or whose outline our
    # erase removed) come back as ONE interior and the English is lettered across both
    # (whole-library audit, v0.27.41: 1,199 of 2,606 blocks on 176 pages).
    given = None
    if shape_mask is not None:
        try:
            _m = np.asarray(shape_mask)
            if _m.ndim == 2 and _m.shape[0] >= gy1 and _m.shape[1] >= gx1:
                _crop_given = _m[gy0:gy1, gx0:gx1].astype(bool)
                if _crop_given.any():
                    given = _crop_given
        except Exception:
            given = None
    if given is not None:
        visited = given
    else:
        crop = gray[gy0:gy1, gx0:gx1] >= light
        ch, cw = crop.shape
        cx0, cy0 = cw // 2, ch // 2
        if not crop[cy0, cx0]:
            # The centre is not light. The page handed here is the INPAINTED one, so the source
            # glyphs are normally gone — but a centre that lands on art inside the balloon, on an
            # un-erased remnant, or on a glyph when the region is tighter than the erase box
            # would otherwise make the whole profile unavailable and hand the block the plain
            # rectangular fit (which has no idea where the outline is). Seed from the nearest
            # light pixel instead: the same trick `analyse_block` uses in the QA screens.
            ys_l, xs_l = np.where(crop)
            if ys_l.size == 0:
                return None
            _k = int(np.argmin((ys_l - cy0) ** 2 + (xs_l - cx0) ** 2))
            cy0, cx0 = int(ys_l[_k]), int(xs_l[_k])

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
    # CLIP the profile to the balloon the ORIGINAL's own outline encloses. The page flooded
    # above is the INPAINTED one, and our erase masks run over the strokes inside a block's
    # box — which takes a balloon's outline with them wherever the box clips it. Job-3
    # p164: the oval's whole top cap was erased, so the flood walked out through the gap
    # and measured the REGION BOX as free space (a flat 206x205 profile), and the first
    # English line was drawn across the arc. On the original the outline is there, so the
    # interior it encloses is the bound the flood should never have crossed.
    #
    # This is a MIN per row: the clip can only ever REMOVE space the flood claimed and
    # never add any, so a page where the two agree is byte-identical, a profile that is
    # already tighter than the balloon (art inside it, a merged neighbour's interior) is
    # left alone, and the region the caller handed us is never enlarged. Growing the
    # region to the balloon box was tried and MEASURED: it inflated the lettering (job-3
    # p85 12px -> 19px, p164 15px -> 17px) and pushed 20 of 135 sampled blocks further
    # outside their balloon, because a balloon whose interior has merged with a
    # neighbour's through a break in the outline hands back a profile spanning both.
    if orig_gray is not None and given is None:
        try:
            ball = balloon_from_original(orig_gray, region, focus=focus, light=light,
                                            box=box)
        except Exception:
            ball = None
        if ball is not None:
            cmask, cbox = ball
            ov = np.zeros_like(sub)
            my0 = cbox[1] - (gy0 + by0)
            mx0 = cbox[0] - (gx0 + bx0)
            sy0, sx0 = max(0, my0), max(0, mx0)
            sy1 = min(sub.shape[0], my0 + cbox[3])
            sx1 = min(sub.shape[1], mx0 + cbox[2])
            if sy1 > sy0 and sx1 > sx0:
                ov[sy0:sy1, sx0:sx1] = cmask[sy0 - my0:sy1 - my0, sx0 - mx0:sx1 - mx0]
                clipped = sub & ov
                # The guard is against a BOGUS enclosure wiping the profile, so it must be
                # measured against the SMALLER of the two shapes: comparing with the flood's
                # own area rejects the clip exactly when the flood is the inflated one, which
                # is the case the clip exists for. MEASURED, job-3 p45: the flood's box starts
                # at y=75 while the balloon's interior starts at y=90, the fit used those
                # rows, and a line was drawn across the top arc.
                _floor = 0.5 * min(sub.sum(), ov.sum())
                if clipped.any() and clipped.sum() >= _floor:
                    _lost = int(sub.sum() - clipped.sum())
                    if _lost > 0:
                        _log("info",
                             "balloon clip: profile %s shed %dpx (%.0f%%) the flood "
                             "claimed outside the original outline",
                             list(region), _lost, 100.0 * _lost / float(sub.sum()))
                    sub = clipped
    # The lettering space in a row is the interior's OWN extent in that row, and the
    # interior's visual centre is the WIDTH-WEIGHTED mean of those rows' centres —
    # never the bounding box centre. An asymmetric interior (a tail, a bulge, an
    # outline merging into a panel border) puts the bbox centre off the balloon's
    # axis: measured on job-3 page 7, bbox centre x=451 vs every row centring on
    # x=468-470, so bbox-centred lettering sat 18px left of the balloon while the
    # commercial reference centres on the axis (x=469.5). Weighting by row width is
    # what makes a tail — many narrow rows — barely move the centre.
    rows_any = sub.any(axis=1)
    if not rows_any.any():
        return None
    x_first = np.where(rows_any, sub.argmax(axis=1), 0)
    x_last = np.where(rows_any, bw - 1 - sub[:, ::-1].argmax(axis=1), 0)
    width = np.where(rows_any, x_last - x_first + 1, 0)
    margin = max(2, int(min(w, h) * margin_frac))
    avail = np.maximum(width - 2 * margin, 0).astype(np.int32)
    wsum = float(width.sum())
    if wsum <= 0:
        return None
    axis_x = float((((x_first + x_last) / 2.0) * width).sum() / wsum)
    axis_y = float((np.arange(bh, dtype=np.float64) * width).sum() / wsum)
    dy_shift = int(round(axis_y - bh / 2.0))
    # ALIGN THE PROFILE WITH THE BOX WE HAND OVER. The box is placed around the balloon's
    # AXIS, not around the sub's top row, so the two disagree by `dy_shift` rows — and an
    # unaligned pair tells the fitter that rows ABOVE the balloon are as wide as the
    # balloon's own waist. MEASURED, job-3 p45 (`Though I don't know if I'll come again next
    # time...`): the returned box's top was 15px above the interior's first row, the profile
    # still reported 116px of width up there, the fit used them, and a line was drawn across
    # the top arc. The x axis needs no such rotation: `avail` is a WIDTH per row, and the
    # fitter centres each line inside the box (which is already on the balloon's axis).
    # avail_new[r] must be the width at page row (box top + r) = the old row (r + dy_shift):
    # rows the box reaches BEYOND the interior's own rows have no measured width at all, and
    # saying 0 there is the whole point (that is the row the fitter must not use).
    if dy_shift < 0:
        _k = min(-dy_shift, bh)
        avail = np.concatenate([np.zeros(_k, np.int32), avail[:max(0, bh - _k)]])
    elif dy_shift > 0:
        _k = min(dy_shift, bh)
        avail = np.concatenate([avail[_k:], np.zeros(_k, np.int32)])
    box = (int(round(gx0 + bx0 + axis_x - bw / 2.0)),
           int(round(gy0 + by0 + axis_y - bh / 2.0)),
           bw, bh)
    # How far the box had to move off the interior's bbox — logged per block so an
    # off-centre lettering defect is visible in the dump instead of only to the eye.
    try:
        _last_shape.clear()
        _last_shape.update({"dx": int(round(axis_x - bw / 2.0)),
                            "dy": int(round(axis_y - bh / 2.0))})
    except Exception:
        pass
    return avail, box


def _line_metrics(probe: ImageDraw.ImageDraw, lines: list[str], font,
                  spacing: int = 2, stroke_width: int = 0):
    """`(widths, rows)` for wrapped `lines`: each line's ink width and its rows in the block.

    `rows[i] = (y0, y1)` are the rows line `i`'s ink actually occupies, measured from the
    block's own top. PIL advances by a CONSTANT per line — the reference line's height plus
    `spacing`, the same for every line whatever its own ink height (measured on Anime Ace at
    24px: lines of 27/26/27 ink measure 89px in total = 2 x 31 + 27) — so the advance is read
    off a two-line reference probe rather than assumed. This matters: the fitter validates
    each line against the balloon's profile AT ITS OWN ROWS, and validating against a
    uniform-band approximation is what let a line slip over an arc in the synthetic
    star-balloon test (64px of 4021) even after the fit said it was inside.
    """
    widths: list[int] = []
    heights: list[int] = []
    for ln in lines:
        bb = probe.textbbox((0, 0), ln, font=font, stroke_width=stroke_width)
        widths.append(int(bb[2] - bb[0]))
        heights.append(max(1, int(bb[3] - bb[1])))
    if not lines:
        return widths, []
    ref = "A"
    b1 = probe.textbbox((0, 0), ref, font=font, stroke_width=stroke_width)
    b2 = probe.multiline_textbbox((0, 0), ref + "\n" + ref, font=font, spacing=spacing,
                                  stroke_width=stroke_width)
    adv = int(round((b2[3] - b2[1]) - (b1[3] - b1[1])))
    adv = max(1, adv)
    rows = [(i * adv, i * adv + heights[i]) for i in range(len(lines))]
    return widths, rows


def _line_widths(probe: ImageDraw.ImageDraw, lines: list[str], font) -> list[int]:
    """Each wrapped line's own ink width (including the white-outline stroke)."""
    return _line_metrics(probe, lines, font, stroke_width=max(1, font.size // 8))[0]


def _placement_top(rows: list[tuple], widths: list[int], avail, region_h: int,
                   tol: float = 1.0, pad_rows: int = 0, step: int = 1):
    """Where to put the block so EVERY line keeps to the balloon's profile.

    `rows[i] = (y0, y1)` is line `i`'s own ink band inside the block (see
    `_line_metrics`). Returns the block's top row inside the region (0 = flush with the
    region's top), or None when no position satisfies all lines.

    THIS IS THE FIX FOR THE OUTLINE-CROSSING CLASS, and it is a change of PLACEMENT, not
    of strictness. A tapered/tailed balloon is narrow at the top and bottom, so a block
    centred in it is fitted to its narrowest band and either shrinks (the v0.27.34
    regression: job-3 p10's dialogue 25px -> 18px, rejected by eye) or — with the width
    test alone — has its first line drawn out across the arc (job-3 p164). Sliding the
    block to the rows where the balloon is actually wide keeps the SIZE and removes the
    crossing.

    The centred position is tried FIRST and the search only moves as far as it must, so a
    balloon that already fits its block is placed exactly as before (and a page where the
    profile is unchanged is byte-identical). `step` > 1 coarsens the search for very tall
    regions; the caller has already proved the centred position fails before paying for it.
    """
    if not widths or not rows or len(widths) != len(rows) or len(avail) == 0:
        return None
    na = len(avail)
    th = rows[-1][1] - rows[0][0]
    if region_h < 8 or th <= 0:
        return None
    need = [int(math.ceil(w / max(tol, 1e-6))) for w in widths]
    max_top = max(0, int(region_h - th))
    centred = max(0, (region_h - th) // 2)

    def fits(top: int) -> bool:
        for (y0, y1), nd in zip(rows, need):
            a = max(0, min(top + y0 - pad_rows, na - 1))
            b = max(a + 1, min(top + y1 + pad_rows, na))
            if int(avail[a:b].min()) < nd:
                return False
        return True

    if fits(centred):
        return centred
    step = max(1, int(step))
    cands = list(range(0, max_top + 1, step))
    if cands and cands[-1] != max_top:
        cands.append(max_top)
    cands.sort(key=lambda t: (abs(t - centred), t))
    for t in cands:
        if fits(t):
            return t
    return None


def _lines_fit_shape(probe, lines: list[str], font, avail, region_h: int, th: int,
                     tol: float = 1.0, pad_rows: int = 0) -> bool:
    """True if every wrapped line fits the balloon's width at its own row band.

    `tol` multiplies the allowed width (see `SHAPE_TOL`). `pad_rows` widens each band
    vertically by the glyph outline's half-thickness: the white halo is drawn OUTSIDE
    the glyph box, so a line sitting exactly on the band's edge puts halo pixels into
    the rows above/below it — on a spiky balloon those rows are much narrower and the
    halo nibbles the outline (measured: 16 stray pixels on a 24-point star at every
    tolerance until the bands covered the halo). Band rows are clipped to the profile's
    length: `avail` has one entry per row of the INTERIOR box while `region_h` is the
    box handed to the fitter, and on a looser region the two differ — indexing past the
    end would silently read an empty slice and reject every candidate, which is how a
    fit collapses for no visible reason.
    """
    n = len(lines)
    if n == 0:
        return False
    na = len(avail)
    if na == 0:
        return False
    top = max(0, (region_h - th) // 2)
    band = th / n
    for i, ln in enumerate(lines):
        y0 = max(0, min(int(top + i * band) - pad_rows, na - 1))
        y1 = max(y0 + 1, min(int(top + (i + 1) * band) + pad_rows, na))
        allowed = int(avail[y0:y1].min()) * tol
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
    fallback_font_path: str | None = None,
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

    # CLASS C SIGNAL (discovery 2026-09-19). A region far too small for the text it
    # carries means the detector's box was wrong — large hand-lettered lines come back
    # as thin slivers (measured: 197x8 for brush text ~70px tall). The Japanese then
    # gets erased and the English is lettered at the 8px fallback inside the sliver,
    # leaving a blank area. Count it so it is auditable, never silent.
    if fitlog.enabled() and len(text or "") >= 8 and min(w, h) < 14:
        # Same rule: instrumentation must never break a render.
        try:
            fitlog.record_degenerate(bbox, text, "thin_region",
                                     {"w": int(w), "h": int(h), "chars": len(text or "")})
        except Exception:
            pass

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
    def _fit_face(fp: str | None):
        """The best fit for ONE face — shape profile first, rectangle net, then the box."""
        _f = None
        if avail is not None and abs(angle) < 3.0:
            _f = _fit_shape(text, avail, h, fp, max_font=max_font)
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
                rect = _fit(text, rect_w, rect_h, fp, max_font=max_font)
                if rect is not None and (_f is None or rect[0] > _f[0]):
                    _f = rect
        if _f is None:
            _f = _fit(text, max_w, max_h, fp, max_font=max_font)
        return _f

    fitted = _fit_face(font_path)
    # --- v0.27.41 LEGIBILITY FALLBACK ------------------------------------------------
    # If the configured face cannot letter this block legibly (the fit landed under the
    # floor) and a NARROWER bundled face can do better IN THE SAME BOX, use it. Nothing
    # moves, nothing shrinks: this is the only change that can make an 8px stat line
    # readable without touching the page's layout. See `fonts.resolve_legibility_fallback`
    # for the measured basis and the face order.
    _face_used, _fallback_used = font_path, False
    if fallback_font_path and (fitted is None or fitted[0] < _LEGIBLE_FLOOR):
        _alt = _fit_face(fallback_font_path)
        if _alt is not None and (fitted is None or _alt[0] > fitted[0]):
            _log("info", "LEGIBILITY fallback face=%s -> %s size=%s -> %d box=%dx%d chars=%d",
                 os.path.basename(font_path or "?"), os.path.basename(fallback_font_path),
                 (fitted[0] if fitted else None), _alt[0], w, h, len(text or ""))
            _face_used, fitted, _fallback_used = fallback_font_path, _alt, True
        elif fitted is not None:
            # The fallback lost. `_fit_*` leaves module state (`_last_fit`/`_last_shape`)
            # describing whichever fit ran LAST, and `_draw_box` reads it for the trace
            # and the placement — so re-run the fit we are actually keeping, or the block
            # gets lettered with the losing face's placement.
            fitted = _fit_face(font_path)
    if fitted is None:
        font = ImageFont.truetype(_face_used, 8)
        lines = _wrap(draw, text, font, max_w)
    else:
        _size, lines, font = fitted

    joined = "\n".join(lines)

    # WHERE the block goes. `bbox` already carries the balloon's visual centre: when
    # the shape path is in use, `typeset_page` replaces the region with the box that
    # `_region_avail` re-centred on the interior's own centroid (bbox centre is
    # skewed by a tail/bulge — see there). So the plain box centre is correct here,
    # and the slack is split evenly instead of pooling at one end.

    # Phase 0 instrumentation (off unless the container was opted in): record what
    # this block got vs the largest size that would have fitted, plus the full size
    # table for blocks that under-sized. See `app/pipeline/fitlog.py`.
    _axis = _last_shape.get("dy")
    _axis_dx = _last_shape.get("dx")
    _last_shape.clear()
    # WHERE the fit decided the block goes inside its region (None = centred). This is
    # the placement half of the outline-crossing fix: a tapered balloon's block slides to
    # the rows whose profile is wide enough instead of shrinking (see `_placement_top`).
    # It is only valid when the last fit was for THIS text, the same guard the trace uses.
    _fit_top = None
    if not _last_fit.get("stale", True) and _last_fit.get("text") == text:
        _fit_top = _last_fit.get("top")
    if fitlog.enabled():
        _chosen = int(font.size) if fitted is not None else 8
        # A trace only belongs to this block if the last fit was for THIS text. A
        # degenerate region makes `_fit_common` bail before it traces anything, and
        # reporting the previous block's numbers against this one produced phantom
        # audit failures (7 of them) — so mark those unmeasurable instead.
        _trace_ok = (not _last_fit.get("stale", True)) and _last_fit.get("text") == text
        fitlog.record_block(bbox, text, bbox,
                            _last_fit.get("avail_max") if _trace_ok else None,
                            _last_fit.get("avail_rows_usable") if _trace_ok else None,
                            _last_fit.get("max_w") if _trace_ok else max_w,
                            _last_fit.get("max_h") if _trace_ok else max_h,
                            _chosen,
                            _last_fit.get("largest_fitting") if _trace_ok else None,
                            bool(_last_fit.get("avail_used")) if _trace_ok else False,
                            len(lines), trace_ok=_trace_ok,
                            wrap=_last_fit.get("wrap") if _trace_ok else None,
                            frac=_last_fit.get("frac") if _trace_ok else None,
                            shape_tol=SHAPE_TOL if (avail is not None and _trace_ok) else None,
                            anchor_dy=None if _axis is None else int(_axis),
                            anchor_dx=None if _axis_dx is None else int(_axis_dx),
                            face=os.path.basename(_face_used) if _face_used else None,
                            fallback=_fallback_used)
        if _trace_ok:
            fitlog.record_candidates(_last_fit.get("table") or [], _chosen,
                                     _last_fit.get("largest_fitting"), text,
                                     bool(_last_fit.get("avail_used")))
    sw = max(1, font.size // 8)

    if abs(angle) < 3.0:
        _y_mid = y + h // 2
        if _fit_top is not None:
            # The fit chose a vertical position inside the region (it slid the block to a
            # row band the balloon's profile is wide enough for) — honour it, or the
            # placed-not-fitted guarantee is lost and the lines go back across the arc.
            try:
                _probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
                _bb2 = _probe.multiline_textbbox((0, 0), joined, font=font, spacing=2,
                                                 align="center", stroke_width=sw)
                _y_mid = y + int(_fit_top) + int(_bb2[3] - _bb2[1]) // 2
            except Exception:
                _y_mid = y + h // 2
        draw.multiline_text(
            (x + w // 2, _y_mid),
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
    shape_masks: dict | None = None,
    page_label: str = "?",
    page_gray: "np.ndarray | None" = None,
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

    `page_gray` is the ORIGINAL page in grayscale. Balloons are measured on IT
    (`balloon_from_original`), because `image` is the INPAINTED page and our own erase
    masks may have removed part of a balloon's outline before the fitter ever measures
    it — that is how job-3 p164's first line came to be drawn across the arc. Omitted ⇒
    every profile comes from the inpainted page, exactly as before.
    """
    out = image.copy()
    draw = ImageDraw.Draw(out)
    fitlog.set_page_label(page_label)
    fp = font_path or resolve_font_path(font_id)
    # Legibility fallback (v0.27.41): the narrower bundled face used ONLY for a block the
    # configured face cannot letter at `_LEGIBLE_FLOOR`. Resolved once per page; never
    # used for drawn SFX, which is art lettered in the configured display face on purpose.
    fb = resolve_legibility_fallback(fp, font_id) if fp else None
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
            # `orig_gray` clips the profile to the balloon the ORIGINAL's outline encloses
            # (see `_region_avail`): the page measured here is the INPAINTED one, and our
            # own erase masks may have taken part of that outline away — job-3 p164, whose
            # first line was drawn across an arc the erase had removed. `focus` is the
            # text's own box centre, because where the text is, the balloon is.
            shape = _region_avail(
                gray, region, orig_gray=page_gray,
                focus=(int(b.bbox[0] + b.bbox[2] // 2),
                       int(b.bbox[1] + b.bbox[3] // 2)),
                box=tuple(int(v) for v in b.bbox),
                shape_mask=(shape_masks or {}).get(id(b)))
            if shape is not None:
                avail, ebox = shape
                # letter into the balloon's OWN box: it is centred on the balloon,
                # not on the (possibly looser) detection box
                region = ebox
                # CLASS C FLOOR (2026-09-19): the flooded interior can come back
                # SMALLER than the block's own text box. Measured on job-3 page 163:
                # ebox 197x8 for a hand-lettered 197x39 line, so the English was drawn
                # at 8px over a large blank area. The detected text box is a FLOOR for
                # the lettering region — never letter into less than where the text was.
                # Only the PATHOLOGICAL case: a region too thin to hold any text
                # (min side < 14). A balloon merely narrower/shorter than the block
                # box is normal and must not be disturbed.
                if min(int(region[2]), int(region[3])) < 14 and (
                        region[2] < b.bbox[2] or region[3] < b.bbox[3]):
                    _x0 = min(region[0], b.bbox[0])
                    _y0 = min(region[1], b.bbox[1])
                    _x1 = max(region[0] + region[2], b.bbox[0] + b.bbox[2])
                    _y1 = max(region[1] + region[3], b.bbox[1] + b.bbox[3])
                    _floored = (_x0, _y0, max(1, _x1 - _x0), max(1, _y1 - _y0))
                    if fitlog.enabled():
                        try:
                            fitlog.record_degenerate(
                                b.bbox, text, "region_floor_applied",
                                {"ebox": [int(v) for v in ebox],
                                 "block_box": [int(v) for v in b.bbox],
                                 "floored": [int(v) for v in _floored]})
                        except Exception:
                            pass
                    region = _floored
        # CLASS C (2026-09-19). A region too thin to hold its text means the box itself
        # is degenerate — measured on job-3 page 163: a 197x8 box carrying a 25-character
        # line, which reached here with `avail is None` (so it is not the `ebox` path and
        # never passes through `targets`), and was lettered at 8px over a large blank
        # area. Recover the real extent from THIS page: the source glyphs are gone by now
        # (we are lettering the inpainted image), so flood the region's surroundings and
        # take the interior box when it is meaningfully bigger and still sane. If the
        # flood leaks, `_region_avail` returns None and nothing changes.
        if min(int(region[2]), int(region[3])) < 14 and len(text or "") >= 8:
            try:
                if gray is None:
                    gray = np.asarray(out.convert("L"))
                _grow = (max(0, int(region[0]) - 24), max(0, int(region[1]) - 48),
                         int(region[2]) + 48, int(region[3]) + 96)
                _shr = _region_avail(gray, _grow)
                if _shr is not None:
                    _eb = tuple(int(v) for v in _shr[1])
                    _ra = int(region[2]) * int(region[3])
                    _na = _eb[2] * _eb[3]
                    if _na > _ra and _na <= 30 * max(1, _ra):
                        _old = tuple(int(v) for v in region)
                        region = _eb
                        avail = None
                        if fitlog.enabled():
                            try:
                                fitlog.record_degenerate(
                                    b.bbox, text, "region_recovered_from_page",
                                    {"old": list(_old), "new": list(region)})
                            except Exception:
                                pass
            except Exception:
                pass
        # NEVER SILENT (v0.27.36): a block that carries a translation and draws no ink
        # where it was lettered is the "erased with nothing drawn" defect class (job-2
        # p56: an off-page caption box, erased, English never drawn) and it used to be
        # completely invisible — the page just came back with a hole in it. Compare the
        # lettering region before/after the draw; unchanged means nothing landed, and an
        # empty intersection means the region was off-page to begin with.
        _r = [int(v) for v in region]
        _rx0, _ry0 = max(0, _r[0]), max(0, _r[1])
        _rx1 = min(out.width, _r[0] + max(1, _r[2]))
        _ry1 = min(out.height, _r[1] + max(1, _r[3]))
        _pre = None
        if _rx1 > _rx0 and _ry1 > _ry0:
            _pre = np.asarray(out.crop((_rx0, _ry0, _rx1, _ry1))).copy()
        _draw_box(out, draw, region, text, fp, bcap, angle=b.angle, avail=avail,
                  fallback_font_path=(None if getattr(b, "is_sfx", False) else fb))
        if _pre is None:
            _log("warning",
                 "NOT LETTERED: lettering region %s is entirely off-page (block box %s, "
                 "page %dx%d) — erased with nothing drawn",
                 _r, tuple(int(v) for v in b.bbox), out.width, out.height)
        else:
            _post = np.asarray(out.crop((_rx0, _ry0, _rx1, _ry1)))
            if _post.shape == _pre.shape and not (_post != _pre).any():
                _log("warning",
                     "NOT LETTERED: region %s unchanged after the draw (block box %s, "
                     "%d chars) — nothing landed where the text was lettered",
                     [_rx0, _ry0, _rx1 - _rx0, _ry1 - _ry0],
                     tuple(int(v) for v in b.bbox), len(text or ""))
    fitlog.end_page()
    return out
