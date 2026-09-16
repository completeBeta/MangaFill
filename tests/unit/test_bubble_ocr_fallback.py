"""Bubbles the text detector missed: OCR the balloon interior instead.

A balloon holding a lone glyph (「真」 with furigana まこと beside it) is not a text
LINE, so neither `text_bubble` nor `text_free` fires and nothing OCRs it — RT-DETR
still finds the balloon, so "bubble with no text block inside" is the signature.
Job-5 page 3's 真 bubble stayed Japanese through every release because of this.

NOTE on the fixtures: all drawing happens on a PIL image that is converted to an
array afterwards. `ImageDraw.Draw(Image.fromarray(arr))` does NOT reliably mutate
the original array (PIL may work on a copy), which silently produced blank fixtures.
"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.render import _bubble_without_text, _ocr_bubbles_without_text
from app.pipeline.types import TextBlock


def _page(bubbles, glyph_size=None):
    """White page with the given balloons; optional black glyph in the first one."""
    im = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (x, y, w, h) in bubbles:
        d.ellipse([x, y, x + w, y + h], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    if glyph_size:
        x, y, w, h = bubbles[0]
        cx, cy = x + w // 2, y + h // 2
        d.rectangle([cx - glyph_size // 2, cy - glyph_size // 2,
                     cx + glyph_size // 2, cy + glyph_size // 2], fill=(0, 0, 0))
    return np.asarray(im)


def test_fixture_glyph_lands_inside_the_balloon():
    """Guard the fixture itself: the glyph must actually be painted."""
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=30)
    patch = page[135:165, 135:165, 0]
    assert (patch < 128).all(), "fixture is blank — drawing did not stick"


def test_bubble_without_text_detects_coverage():
    bubble = (100, 100, 100, 100)
    inside = TextBlock(bbox=(140, 140, 20, 20), text="x")
    outside = TextBlock(bbox=(300, 300, 20, 20), text="x")
    assert _bubble_without_text(bubble, [])
    assert not _bubble_without_text(bubble, [inside])
    assert _bubble_without_text(bubble, [outside])


def test_missed_bubble_is_ocrd_and_returned():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=30)
    calls = []

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "真", None

    out = _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=fake_ocr)
    assert len(out) == 1 and len(calls) == 1
    b = out[0]
    assert b.text == "真" and b.orientation == "vertical"
    # the block box is the inset balloon interior, so the lettering lands centred
    assert bubble[0] < b.bbox[0] and b.bbox[0] + b.bbox[2] < bubble[0] + bubble[2]


def test_empty_balloon_is_skipped_without_ocr():
    """A balloon with no text must not cost an OCR call or invent text.

    Measured on the deeper inset, so the balloon's own outline (which curves into
    the corners of a shallow inset) is not mistaken for ink — and the OCR model,
    which happily hallucinates kana on an empty balloon, is never asked.
    """
    bubble = (100, 100, 120, 120)
    page = _page([bubble])           # outline only, interior blank
    calls = []

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "なんか", None

    out = _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=fake_ocr)
    assert out == [] and calls == []


def test_bubble_with_existing_block_is_not_reocrd():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=30)
    calls = []
    existing = TextBlock(bbox=(140, 140, 30, 30), text="既存", translation="existing")

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "真", None

    assert _ocr_bubbles_without_text(page, [bubble], [existing], ocr_fn=fake_ocr) == []
    assert calls == []


def test_non_japanese_ocr_result_is_dropped():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=30)
    out = _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=lambda i, b: ("HELLO", None))
    assert out == []


def test_no_bubbles_is_a_noop():
    page = _page([])
    assert _ocr_bubbles_without_text(page, [], []) == []
    assert _ocr_bubbles_without_text(page, None, []) == []
