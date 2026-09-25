"""Class C floor: the lettering region must never be smaller than the block's text box.

job-3 page 163: the flooded interior (`ebox`) came back 197x8 for a hand-lettered
197x39 line, so `region = ebox` made the English 8px over a large blank area.
"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.fonts import resolve_font_path
from app.pipeline.types import TextBlock
from app.pipeline.typeset import typeset_page


def _font():
    fp = resolve_font_path(None)
    assert fp, "no display font available"
    return fp


def _strip_page():
    """A white page with a thin light gap (8px) between two dark bands."""
    img = Image.new("RGB", (600, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 599, 250), fill=(0, 0, 0))
    d.rectangle((0, 258, 599, 399), fill=(0, 0, 0))
    return img


def test_region_is_floored_at_the_block_box():
    """A thin flooded interior must not shrink the region below the text box."""
    _font()
    img = _strip_page()
    b = TextBlock(bbox=(200, 230, 197, 39), text="", translation="It hasn't gone up at all!")
    b.orientation = "horizontal"
    regions = {id(b): (200, 230, 197, 39)}
    out = typeset_page(img, [b], regions=regions, only={id(b)}, shapes={id(b)})
    arr = np.asarray(out.convert("L"))
    # ink that is NOT one of the two black bands = the drawn lettering
    ink = arr[242:266, 200:400] < 90
    rows = np.where(ink.any(axis=1))[0]
    assert len(rows) > 0, "nothing was lettered"
    span = int(rows.max() - rows.min()) + 1
    assert span > 12, (
        f"lettering spans only {span}px — the region was not floored to the 39px block box"
    )


def test_thin_region_without_a_shape_is_untouched():
    """The floor lives in the shape path; a non-shaped block keeps its own region."""
    _font()
    img = _strip_page()
    b = TextBlock(bbox=(200, 230, 197, 39), text="", translation="It hasn't gone up at all!")
    b.orientation = "horizontal"
    regions = {id(b): (200, 230, 197, 39)}
    out = typeset_page(img, [b], regions=regions, only={id(b)})
    assert out.size == (600, 400), "typeset_page must not resize the page"
