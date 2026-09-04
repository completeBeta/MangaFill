"""Unit tests for typeset: min-size fallback never leaves a blank bubble."""
import os

import numpy as np
from PIL import Image

from app.pipeline.typeset import typeset_page
from app.pipeline.types import TextBlock


def _font():
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return p
    return None


def test_long_translation_never_blank():
    fp = _font()
    if fp is None:
        return  # font unavailable on this host; skip
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    b = TextBlock(bbox=(90, 100, 20, 80), text="あ", translation="a very long english sentence that cannot possibly fit", orientation="vertical")
    out = typeset_page(img, [b], font_path=fp)
    # The translation must have been drawn (not skipped) even though it overflows.
    assert out != img


def test_furigana_and_empty_skipped():
    fp = _font()
    if fp is None:
        return
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    furi = TextBlock(bbox=(10, 10, 8, 30), text="かな", translation="reading", orientation="furigana")
    empty = TextBlock(bbox=(100, 10, 20, 30), text="", translation="", orientation="vertical")
    out = typeset_page(img, [furi, empty], font_path=fp)
    assert out == img  # nothing drawn


def test_text_has_white_outline_on_dark_background():
    fp = _font()
    if fp is None:
        return
    # Black background: the black fill is invisible, so any non-black pixels in
    # the box must come from the white outline added behind the glyphs.
    img = Image.new("RGB", (300, 200), (0, 0, 0))
    b = TextBlock(bbox=(50, 50, 200, 100), text="あ", translation="HELLO", orientation="vertical")
    out = typeset_page(img, [b], font_path=fp)
    arr = np.asarray(out.crop((50, 50, 250, 150)))
    white = int(((arr[:, :, 0] > 200) & (arr[:, :, 1] > 200) & (arr[:, :, 2] > 200)).sum())
    assert white > 0
