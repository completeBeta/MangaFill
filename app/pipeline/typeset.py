"""Typeset — render English translations back into text boxes.

English manga lettering is horizontal (LTR), bold, and centered in the balloon.
The hard part is fitting: pick the largest font size whose wrapped lines still
fit the box, then center the block. DejaVu Bold is the v1 placeholder — a proper
manga face (CC Wild Words / Anime Ace) should replace it once sourced.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

from .types import TextBlock

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _find_font() -> str | None:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


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


def _fit(text: str, max_w: int, max_h: int, font_path: str):
    """Largest font size whose wrapped lines fit inside (max_w, max_h).

    Uses multiline_textbbox so the measurement matches what PIL actually draws
    (a getmetrics() estimate drifted from real line advance and caused overlap).
    """
    lo, hi = 8, 200
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


def _draw_box(draw: ImageDraw.ImageDraw, bbox: tuple, text: str, font_path: str | None):
    if not text or not text.strip():
        return
    if not font_path:
        return
    x, y, w, h = bbox
    fitted = _fit(text, w - 4, h - 4, font_path)
    if fitted is None:
        return
    _size, lines, font = fitted
    draw.multiline_text(
        (x + w // 2, y + h // 2),
        "\n".join(lines),
        font=font,
        fill=(0, 0, 0),
        anchor="mm",
        align="center",
        spacing=2,
    )


def typeset_page(image: Image.Image, blocks: list[TextBlock], font_path: str | None = None) -> Image.Image:
    """Draw every translatable block's English translation into a copy of `image`.

    Furigana (ruby) and untranslated blocks (titles/SFX/watermarks) are skipped:
    furigana is erased, not re-lettered; SFX/titles stay as-is.
    """
    out = image.copy()
    draw = ImageDraw.Draw(out)
    fp = font_path or _find_font()
    for b in blocks:
        if b.orientation == "furigana":
            continue
        if not b.translation:
            continue
        _draw_box(draw, b.bbox, b.translation, fp)
    return out
