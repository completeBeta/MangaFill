"""Bubbles the text detector missed: OCR the balloon interior instead.

A balloon holding a lone glyph (「真」 with furigana まこと beside it) is not a text
LINE, so neither `text_bubble` nor `text_free` fires and nothing OCRs it — RT-DETR
still finds the balloon, so "bubble with no text block inside" is the signature.
Job-5 page 3 / job-4 page 7's 真 balloon stayed Japanese through every release.

The guards matter as much as the recovery: OCR'ing artwork hallucinates plausible
Japanese, and a detection box covering only PART of a balloon would otherwise get it
lettered twice. All thresholds here are calibrated against real pages (see the
docstrings).

NOTE on the fixtures: all drawing happens on a PIL image that is converted to an
array afterwards. `ImageDraw.Draw(Image.fromarray(arr))` does NOT reliably mutate
the original array (PIL may work on a copy), which silently produced blank fixtures.
"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.render import (_bubble_covered, _bubble_without_text, _ink_is_glyph_like,
                                 _ocr_bubbles_without_text, _recoverable_text)
from app.pipeline.types import TextBlock


def _page(bubbles, glyph_size=None, glyph_touch=0):
    """White page with balloons; optional black glyph in the first one.

    `glyph_touch` grows the glyph by that many px in each direction so it reaches the
    balloon edge (stands in for artwork that runs to the border of any inset).
    """
    im = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (x, y, w, h) in bubbles:
        d.ellipse([x, y, x + w, y + h], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    if glyph_size:
        x, y, w, h = bubbles[0]
        cx, cy = x + w // 2, y + h // 2
        half = glyph_size // 2 + glyph_touch
        d.rectangle([cx - half, cy - half, cx + half, cy + half], fill=(0, 0, 0))
    return np.asarray(im)


def test_fixture_glyph_lands_inside_the_balloon():
    """Guard the fixture itself: the glyph must actually be painted."""
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=30)
    assert (page[135:165, 135:165, 0] < 128).all(), "fixture is blank — drawing did not stick"


def test_bubble_without_text_detects_coverage():
    bubble = (100, 100, 100, 100)
    inside = TextBlock(bbox=(140, 140, 20, 20), text="x")
    outside = TextBlock(bbox=(300, 300, 20, 20), text="x")
    assert _bubble_without_text(bubble, [])
    assert not _bubble_without_text(bubble, [inside])
    assert _bubble_without_text(bubble, [outside])


def test_bubble_covered_uses_overlap_not_centre():
    """A block whose centre sits BELOW a partial bubble box still covers it.

    Real case (job-4 page 92): the bubble box was the top line of a 3-line caption,
    so the caption block's centre fell outside it. Centre containment said "no text
    here" and the balloon would have been lettered a second time.
    """
    bubble = (792, 834, 228, 93)          # partial: only the caption's top line
    caption = TextBlock(bbox=(776, 858, 235, 176), text="3-line caption",
                        translation="O earth that brings rich harvest")
    assert _bubble_without_text(bubble, [caption])   # centre test says "missed"...
    assert _bubble_covered(bubble, [caption])        # ...overlap test says covered
    far = TextBlock(bbox=(100, 100, 40, 40), text="elsewhere")
    assert not _bubble_covered(bubble, [far])


def test_ink_is_glyph_like_separates_glyph_from_artwork():
    box = (122, 120, 56, 60)              # deeper inset of a 100x100 balloon at 100,100
    glyph = _page([(100, 100, 100, 100)], glyph_size=16)
    assert _ink_is_glyph_like(glyph, box)
    # artwork running to the inset edge is NOT glyph-like
    art = _page([(100, 100, 100, 100)], glyph_size=26, glyph_touch=30)
    assert not _ink_is_glyph_like(art, box)
    # a blank balloon has no ink at all
    assert not _ink_is_glyph_like(_page([(100, 100, 100, 100)]), box)


def test_recoverable_text_rejects_fragments_and_sfx():
    assert _recoverable_text("真")            # lone kanji: a word or a name
    assert _recoverable_text("私")
    assert _recoverable_text("うん．．．")     # short but real dialogue
    assert _recoverable_text("それぞれ、")
    assert not _recoverable_text("の")        # lone kana: a clipped fragment
    assert not _recoverable_text("え")
    assert not _recoverable_text("～～〜〜！？")  # punctuation-only SFX mark
    assert not _recoverable_text("HELLO")     # already English
    assert not _recoverable_text("")


def test_missed_bubble_is_ocrd_and_returned():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=16)     # ~6% ink, like the real 真
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
    """A blank balloon must not cost an OCR call or invent text into itself."""
    bubble = (100, 100, 120, 120)
    page = _page([bubble])           # outline only, interior blank
    calls = []

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "なんか", None

    assert _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=fake_ocr) == []
    assert calls == []


def test_artwork_balloon_is_skipped_without_ocr():
    """A balloon full of artwork must not be OCR'd — manga-ocr hallucinates on it."""
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=70)     # ink reaches the inset border
    calls = []

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "うん", None

    assert _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=fake_ocr) == []
    assert calls == []


def test_oversized_drawn_glyph_is_skipped():
    """A drawn glyph filling the balloon is ART, not dialogue.

    Calibrated on job-4 page 28 (a starburst holding a big drawn kanji: 23% ink
    against 1-11% for real dialogue; the reported 真 measures 6%). The ink cap means
    such a balloon is left alone instead of lettering a misread word over the art.
    """
    bubble = (100, 100, 100, 100)
    # a solid bar across the inset, not touching the border -> glyph-like, but far
    # more ink than any line of dialogue
    im = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([100, 100, 200, 200], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.rectangle([128, 130, 172, 170], fill=(0, 0, 0))
    page = np.asarray(im)
    calls = []
    assert _ocr_bubbles_without_text(page, [bubble], [],
                                     ocr_fn=lambda i, b: (calls.append(b), "識")[1]) == []
    assert calls == []


def test_bubble_with_existing_block_is_not_reocrd():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=16)
    calls = []
    existing = TextBlock(bbox=(140, 140, 30, 30), text="既存", translation="existing")

    def fake_ocr(image, bbox):
        calls.append(bbox)
        return "真", None

    assert _ocr_bubbles_without_text(page, [bubble], [existing], ocr_fn=fake_ocr) == []
    assert calls == []


def test_non_japanese_ocr_result_is_dropped():
    bubble = (100, 100, 100, 100)
    page = _page([bubble], glyph_size=16)
    out = _ocr_bubbles_without_text(page, [bubble], [], ocr_fn=lambda i, b: ("HELLO", None))
    assert out == []


def test_no_bubbles_is_a_noop():
    page = _page([])
    assert _ocr_bubbles_without_text(page, [], []) == []
    assert _ocr_bubbles_without_text(page, None, []) == []
