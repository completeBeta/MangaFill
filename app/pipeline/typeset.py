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
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or _text_w(draw, trial, font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit(text: str, max_w: int, max_h: int, font_path: str, max_font: int = 32):
    """Largest font size (capped at `max_font`) whose wrapped lines fit the box.

    Uses multiline_textbbox so the measurement matches what PIL actually draws
    (a getmetrics() estimate drifted and caused overlap), measuring *with* the
    white-outline stroke so the lettering + stroke stays inside the box (the
    "text spilling over its box" bug). Returns None if even the 8px floor
    overflows.
    """
    lo, hi = 8, max_font
    best = None
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(font_path, mid)
        sw = max(1, mid // 8)
        lines = _wrap(probe, text, font, max_w)
        joined = "\n".join(lines)
        bb = probe.multiline_textbbox(
            (0, 0), joined, font=font, spacing=2, align="center", stroke_width=sw
        )
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw <= max_w and th <= max_h:
            best = (mid, lines, font)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


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
        _draw_box(draw, region, b.translation, fp, cap)
    return out
