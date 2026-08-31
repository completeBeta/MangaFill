"""Unit tests for typeset: min-size fallback never leaves a blank bubble."""
import os

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
