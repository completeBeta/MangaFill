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


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit(text: str, max_w: int, max_h: int, font_path: str, max_font: int = 32):
    """Largest font size (capped at `max_font`) whose wrapped lines fit the box.

    Manga lettering is a bounded, consistent size — not "fill the bubble", which
    produces absurd giant text for short lines in big bubbles. So the search is
    clamped to `max_font`. Uses multiline_textbbox so the measurement matches what
    PIL actually draws (a getmetrics() estimate drifted and caused overlap).
    """
    lo, hi = 8, max_font
    best = None
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(font_path, mid)
        lines = _wrap(probe, text, font, max_w)
        joined = "\n".join(lines)
        bb = probe.multiline_textbbox((0, 0), joined, font=font, spacing=2, align="center")
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw <= max_w and th <= max_h:
            best = (mid, lines, font)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _draw_box(draw: ImageDraw.ImageDraw, bbox: tuple, text: str, font_path: str | None, size: int):
    if not text or not text.strip():
        return
    if not font_path:
        return
    x, y, w, h = bbox
    pad = 4
    max_w = max(w - 2 * pad, 1)
    max_h = max(h - 2 * pad, 1)
    # Render at the uniform page size; shrink ONLY if it overflows the box. This
    # keeps lettering consistent across bubbles (a short line isn't blown up to
    # fill a big bubble).
    font = ImageFont.truetype(font_path, size)
    lines = _wrap(draw, text, font, max_w)
    joined = "\n".join(lines)
    bb = draw.multiline_textbbox((0, 0), joined, font=font, spacing=2, align="center")
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    if tw > max_w or th > max_h:
        fitted = _fit(text, max_w, max_h, font_path, max_font=size)
        if fitted is None:
            # Fallback: smallest size, wrap to width, allow overflow. A slightly
            # overflowing line beats a silently blank bubble.
            font = ImageFont.truetype(font_path, 8)
            lines = _wrap(draw, text, font, max_w)
        else:
            _size, lines, font = fitted
    draw.multiline_text(
        (x + w // 2, y + h // 2),
        joined if tw <= max_w and th <= max_h else "\n".join(lines),
        font=font,
        fill=(0, 0, 0),
        anchor="mm",
        align="center",
        spacing=2,
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
    # Uniform manga lettering size across the page (~1/42 of page width; a 1125px
    # page -> ~27px). A single consistent size beats per-bubble "largest that
    # fits" — that produced a short line blown up to 32px in a big bubble while
    # long dialogue shrank to ~18px. Shrink-to-fit only kicks in on overflow.
    std_font = max(18, image.width // 42)
    for b in blocks:
        if only is not None and id(b) not in only:
            continue
        if b.orientation == "furigana":
            continue
        if not b.translation:
            continue
        region = regions.get(id(b), b.bbox) if regions else b.bbox
        _draw_box(draw, region, b.translation, fp, std_font)
    return out
