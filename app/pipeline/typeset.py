"""Typeset — render English translations back into text boxes.

English manga lettering is horizontal (LTR), bold, and centered in the balloon.
The hard part is fitting: pick the largest font size whose wrapped lines still
fit the box, then center the block.

Font selection lives in `app.pipeline.fonts`: the user-selected face (or the
Anime Ace default) is used when present, then the bundled OFL faces, then
DejaVu Sans Bold. See `resolve_font_path` there.
"""
from __future__ import annotations

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
    return best_clean or best


def _draw_box(draw: ImageDraw.ImageDraw, bbox: tuple, text: str, font_path: str | None, max_font: int):
    if not text or not text.strip():
        return
    if not font_path:
        return
    x, y, w, h = bbox
    pad = 6
    max_w = max(w - 2 * pad, 1)
    max_h = max(h - 2 * pad, 1)
    # Dynamic per-box sizing: the largest size (capped at `max_font`) that fits
    # the box. Short text fills a big bubble; long text shrinks to fit a small
    # one. If nothing fits, fall back to 8px (may overflow) rather than blank.
    fitted = _fit(text, max_w, max_h, font_path, max_font=max_font)
    if fitted is None:
        font = ImageFont.truetype(font_path, 8)
        lines = _wrap(draw, text, font, max_w)
    else:
        _size, lines, font = fitted
    draw.multiline_text(
        (x + w // 2, y + h // 2),
        "\n".join(lines),
        font=font,
        fill=(0, 0, 0),
        anchor="mm",
        align="center",
        spacing=2,
        # White outline behind the glyphs so black lettering stays readable over
        # dark boxes/screentone (stat tables, narration panels). The stroke is
        # sized to the font so it scales cleanly from 8px to the 32px cap.
        stroke_width=max(1, font.size // 8),
        stroke_fill=(255, 255, 255),
    )


def typeset_page(
    image: Image.Image,
    blocks: list[TextBlock],
    font_path: str | None = None,
    font_id: str | None = None,
    regions: dict | None = None,
    only: set | None = None,
) -> Image.Image:
    """Draw every translatable block's English translation into a copy of `image`.

    Furigana (ruby) and untranslated blocks (titles/SFX/watermarks) are skipped:
    furigana is erased, not re-lettered; SFX/titles stay as-is.

    `regions` optionally maps id(block) -> (x, y, w, h) container (the bubble/box
    interior) to draw into. When omitted, each block's own bbox is used.

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
    for b in blocks:
        if only is not None and id(b) not in only:
            continue
        if b.orientation == "furigana":
            continue
        if not b.translation:
            continue
        region = regions.get(id(b), b.bbox) if regions else b.bbox
        bcap = cap
        if b.orientation == "horizontal":
            # Horizontal captions/stat lines letter at a size tied to their own
            # text height, not the full-page cap — a short footnote should not
            # blow up to the max size just because the caption region is wide.
            bcap = min(cap, max(14, int(b.bbox[3] * 0.8)))
        _draw_box(draw, region, b.translation, fp, bcap)
    return out
