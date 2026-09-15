"""Drawn sound effects must be lettered to occupy the space they occupied.

The font cap in `typeset_page` came from the PAGE width (~width/32 = 21px on a
690px webtoon page), so a page-wide drawn sound effect was erased and replaced by
a 21px word floating in the inpainted smear: job-2 page 85's `조~으~옹..` — a
421px-tall piece of art — became a 21px "Quiet.".
"""
import os

import numpy as np
from PIL import Image

from app.pipeline.render import _is_drawn_sfx
from app.pipeline.types import TextBlock
from app.pipeline.typeset import typeset_page


def _font():
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return p
    return None


def _ink_height(img: Image.Image) -> int:
    """Height of the drawn (dark) glyph band."""
    a = np.asarray(img.convert("L"))
    rows = np.where((a < 100).sum(axis=1) > 0)[0]
    return int(rows[-1] - rows[0] + 1) if len(rows) else 0


def test_sfx_is_lettered_to_fill_its_box():
    fp = _font()
    if fp is None:
        return  # font unavailable on this host; skip
    box = (10, 10, 660, 400)          # a big drawn sound effect on the art
    img = Image.new("RGB", (690, 430), (255, 255, 255))
    plain = typeset_page(img, [TextBlock(bbox=box, text="조용",
                                         translation="Quiet.",
                                         orientation="horizontal")],
                         font_path=fp)
    sfx = typeset_page(img, [TextBlock(bbox=box, text="조용",
                                       translation="Quiet.",
                                       orientation="horizontal", is_sfx=True)],
                       font_path=fp)
    assert _ink_height(sfx) > 4 * _ink_height(plain), (
        f"sfx={_ink_height(sfx)}px vs plain={_ink_height(plain)}px — the box-derived "
        "cap is not being applied"
    )
    # and it must stay inside the box it was given
    a = np.asarray(sfx.convert("L"))
    rows = np.where((a < 100).sum(axis=1) > 0)[0]
    cols = np.where((a < 100).sum(axis=0) > 0)[0]
    assert rows[0] >= box[1] and rows[-1] <= box[1] + box[3]
    assert cols[0] >= box[0] and cols[-1] <= box[0] + box[2]


def test_sfx_text_is_uppercased():
    fp = _font()
    if fp is None:
        return
    box = (10, 10, 200, 120)
    img = Image.new("RGB", (300, 200), (255, 255, 255))
    out = typeset_page(img, [TextBlock(bbox=box, text="쿵", translation="thud",
                                       orientation="horizontal", is_sfx=True)],
                       font_path=fp)
    cap = typeset_page(img, [TextBlock(bbox=box, text="쿵", translation="THUD",
                                       orientation="horizontal", is_sfx=True)],
                       font_path=fp)
    # rendered identically because the SFX path upper-cases before drawing
    assert np.array_equal(np.asarray(out), np.asarray(cap))


# ------------------------------------------------------------------ classification

def test_is_drawn_sfx_accepts_a_short_wide_glyph_block():
    b = TextBlock(bbox=(0, 680, 690, 421), text="조용", orientation="horizontal")
    assert _is_drawn_sfx(b)


def test_is_drawn_sfx_rejects_long_text():
    """Long free text is a caption — it keeps the caption treatment."""
    b = TextBlock(bbox=(0, 0, 400, 200),
                  text="아파트 베란다에서도 쉽게 재배가능!", orientation="horizontal")
    assert not _is_drawn_sfx(b)


def test_is_drawn_sfx_rejects_vertical_text():
    """Vertical art text still needs `_caption_region`'s widening."""
    b = TextBlock(bbox=(100, 100, 60, 400), text="곳", orientation="vertical")
    assert not _is_drawn_sfx(b)


def test_is_drawn_sfx_rejects_tiny_labels():
    b = TextBlock(bbox=(10, 10, 40, 20), text="난이도", orientation="horizontal")
    assert not _is_drawn_sfx(b)
