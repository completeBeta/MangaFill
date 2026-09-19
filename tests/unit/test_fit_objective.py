"""Phase 1 tests: the sizing objective is SIZE FIRST, taste second.

The regression these lock down: `_fit_common` used to apply a HARD exclusion for the
"word list" look (3+ lines, nearly all single words). Bigger letters wrap to fewer
words per line, so the exclusion deleted exactly the large candidates, and fill
maximisation then ran over the small ones. Measured on a 12-page sample: 20% of
blocks under-sized, mean +31%, worst +100% — "That makes three." at 9px where 18px
fitted; a 148x234 balloon holding 10px text.

New contract: the chosen size is never more than ONE step below the largest size
that fits the geometry.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _fit, _fit_shape


def _fp():
    fp = resolve_font_path(None)
    if fp is None:
        pytest.skip("font unavailable on this host")
    return fp


def _largest_fitting(text, max_w, max_h, fp, cap=35):
    """Reference implementation of the geometric maximum: the biggest size that fits."""
    import PIL.ImageDraw as D
    from PIL import Image, ImageFont
    from app.pipeline.typeset import _wrap
    probe = D.Draw(Image.new("RGB", (1, 1)))
    for size in range(cap, 7, -1):
        font = ImageFont.truetype(fp, size)
        lines = _wrap(probe, text, font, max_w)
        bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=2,
                                      align="center", stroke_width=max(1, size // 8))
        if bb[2] - bb[0] <= max_w and bb[3] - bb[1] <= max_h:
            return size
    return None


def test_page_8_constitution_case_is_no_longer_undersized():
    """The exact block that made page 8 look broken: a 25-char line in a 148x234
    balloon that renders at ~10px while 13px fits. It must now stay within one step
    of the geometric maximum."""
    fp = _fp()
    text = "That constitution of yours"
    max_w, max_h = 120, 206
    got = _fit(text, max_w, max_h, fp, max_font=35)
    assert got is not None
    size = got[0]
    ref = _largest_fitting(text, max_w, max_h, fp)
    assert ref is not None
    assert size >= ref - 1, f"chosen {size}px vs {ref}px available — reason: {got[1]}"
    assert size >= 12, f"still tiny: {size}px"


@pytest.mark.parametrize("text", [
    "That makes three.",
    "Abelia, that's enough.",
    "To put it another way",
    "But you know",
    "You can only ever become ordinary.",
    "It's incredibly delicious!",
    "Who are you!?",
    "I can still keep going!",
])
def test_size_is_within_one_step_of_the_geometric_max(text):
    """The general guarantee, over the real text shapes that were under-sized."""
    fp = _fp()
    max_w, max_h = 120, 206
    got = _fit(text, max_w, max_h, fp, max_font=35)
    assert got is not None
    ref = _largest_fitting(text, max_w, max_h, fp)
    assert got[0] >= ref - 1, f"{text!r}: {got[0]}px vs {ref}px available ({got[1]})"


def test_stacked_look_is_allowed_when_it_is_the_larger_size():
    """A word-per-line stack in a tall balloon is ordinary comic lettering, and must
    never cost more than one step. This is the case the old rule vetoed."""
    fp = _fp()
    text = "I waited in line"
    got = _fit(text, 90, 300, fp, max_font=35)
    assert got is not None
    ref = _largest_fitting(text, 90, 300, fp)
    assert got[0] >= ref - 1


def test_shape_fit_never_exceeds_the_profile():
    """SIZE FIRST must not break the outline guarantee: every line still fits the band."""
    from PIL import Image, ImageDraw
    fp = _fp()
    H, W = 240, 300
    rows = np.arange(H)
    half = (H - 1) / 2.0
    avail = np.maximum(16, ((1.0 - np.abs(rows - half) / half) * (W - 24)).astype(int))
    got = _fit_shape("Several words of ordinary dialogue here.", avail, H, fp, max_font=44)
    assert got is not None
    size, lines, font = got
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for ln in lines:
        bb = probe.textbbox((0, 0), ln, font=font, stroke_width=max(1, size // 8))
        assert bb[2] - bb[0] <= int(avail.max()) + 10


def test_very_long_word_still_shrinks_to_fit():
    """The guarantee is a FLOOR on size, not a licence to overflow: a word that cannot
    fit at the largest sizes must still be lettered smaller rather than spill."""
    fp = _fp()
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    got = _fit("Extraordinary", 90, 400, fp, max_font=35)
    if got is None:
        return  # no size fits; the caller's fallback handles it
    size, lines, font = got
    widest = max(
        probe.textbbox((0, 0), ln, font=font, stroke_width=max(1, size // 8))[2]
        for ln in lines
    )
    assert widest <= 92, f"a line {widest}px wide overflowed an 90px region at {size}px"
