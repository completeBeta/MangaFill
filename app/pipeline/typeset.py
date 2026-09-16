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

from .fonts import resolve_font_path
from .types import TextBlock


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
    """
    words = text.split()
    if not words:
        return [""]
    n = len(words)
    if n == 1:
        return words

    # Exact width of every candidate line words[i:j] (stroke included, via the
    # same _text_w the greedy path used, so monkeypatched tests stay valid).
    wd = [[0] * (n + 1) for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n + 1):
            wd[i][j] = _text_w(draw, " ".join(words[i:j]), font)

    inf = float("inf")
    cost = [inf] * (n + 1)
    brk = [0] * (n + 1)
    cost[n] = 0.0
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n + 1):
            single = (j - i == 1)
            if wd[i][j] > max_w and not single:
                break  # multi-word line too wide; a wider j only grows
            if wd[i][j] > max_w:
                # A lone word wider than the box is forced onto its own line —
                # heavy penalty so the DP avoids it, but it keeps every position
                # breakable (never an infinite reconstruction loop).
                bad = 2.0
            elif j == n:
                # Last line: penalise a lone trailing word so it joins the line
                # above when it fits (0.6 > the max per-line slack² of 1.0).
                bad = 0.6 if single else 0.0
            else:
                slack = (max_w - wd[i][j]) / max_w
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


def _fit(text: str, max_w: int, max_h: int, font_path: str, max_font: int = 32):
    """Best font size for `text` in the box: the largest size whose wrapped lines
    fit *and* carry no single-word line, else the largest that fits.

    The old binary search maximised size alone, so in a narrow bubble it picked a
    size where each word lands on its own line ("I / WAITED / IN LINE / …").
    A single-word line is the visual marker that the font is too big for the
    width, so we prefer the largest size with none of them (failing open to the
    largest fitting size when a lone word can't pair at any size — e.g. one very
    long word, or a genuinely narrow box). Linear scan over the small 8..max_font
    range is cheaper than the old binary search's correctness anyway.
    """
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    best: tuple | None = None        # (size, lines, font) — largest fitting size
    best_clean: tuple | None = None  # largest fitting size with no single-word line
    for size in range(max_font, 7, -1):
        font = ImageFont.truetype(font_path, size)
        sw = max(1, size // 8)
        lines = _wrap(probe, text, font, max_w)
        joined = "\n".join(lines)
        bb = probe.multiline_textbbox(
            (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
        )
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw > max_w or th > max_h:
            continue
        if best is None:
            best = (size, lines, font)
        singles = sum(1 for ln in lines if len(ln.split()) == 1)
        if singles <= 1:
            best_clean = (size, lines, font)
            break  # largest size with at most one lone-word line (scanning high -> low)
    # NOTE (v0.27.8): the "prefer filling" rule added in v0.27.7 was removed from
    # this rectangular path. It blew up text in regions that have NO boundary to
    # respect — caption strips over artwork, free-floating mutter text — where a
    # wide strip let a 1-line caption balloon to the page font cap and collide
    # with neighbouring panels (job-4 page 10). Filling is now done properly by
    # `_fit_shape`, which fits to the balloon's actual outline; a region with no
    # shape keeps the aesthetic "no lone-word line" sizing it had in v0.27.6.
    return best_clean or best


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


def _fit_shape(text: str, avail, region_h: int, font_path: str, max_font: int = 32):
    """Largest font size whose wrapped lines all stay inside the balloon's shape.

    This is what lets lettering FILL a balloon without escaping it: the target is
    the shape, not the bounding box, so a big round balloon can be lettered much
    larger than a rectangular inset would allow, while a spiky or oval one is
    still never overrun.
    """
    max_w = int(avail.max())
    if max_w < 12 or region_h < 8:
        return None
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for size in range(max_font, 7, -1):
        font = ImageFont.truetype(font_path, size)
        sw = max(1, size // 8)
        lines = _wrap(probe, text, font, max_w)
        joined = "\n".join(lines)
        bb = probe.multiline_textbbox(
            (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
        )
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw > max_w or th > region_h:
            continue
        if _lines_fit_shape(probe, lines, font, avail, region_h, th):
            return (size, lines, font)
    return None


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
    inset = max(int(min(w, h) * 0.15), 6)
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
    # the box. Short text fills a big bubble; long text shrinks to fit a small
    # one. If nothing fits, fall back to 8px (may overflow) rather than blank.
    #
    # When the caller knows the box's actual shape (`avail`, from `_region_avail`)
    # the fit targets that shape instead of the rectangle: lettering fills an oval
    # balloon to its curve without ever crossing the outline. Rectangles remain
    # the fallback for slanted boxes and for regions with no enclosing shape.
    fitted = None
    if avail is not None and abs(angle) < 3.0:
        fitted = _fit_shape(text, avail, h, font_path, max_font=max_font)
    if fitted is None:
        fitted = _fit(text, max_w, max_h, font_path, max_font=max_font)
    if fitted is None:
        font = ImageFont.truetype(font_path, 8)
        lines = _wrap(draw, text, font, max_w)
    else:
        _size, lines, font = fitted

    joined = "\n".join(lines)
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
    return out
