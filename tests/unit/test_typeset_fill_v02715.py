"""Lettering must FILL the box it is given (v0.27.15).

v0.27.14 and earlier sized lettering with "the largest size that fits *and* has at
most one single-word line". Whenever a clean-looking wrapping existed anywhere
below the cap, the fitter took it and left the box mostly empty. Audited on job 1
(Chinese manhua, real blocks from the prod DB):

    'Ah'                        in 133x138   -> 36px  (13% of the box)
    "I'm sorry, big sister-"    in 269x475   -> 25px  ( 9%)
    'Great fortune'             in 196x313   -> 14px  ( 3%)
    'I won't go back with you!' in 244x403   -> 27px  (17%)

v0.27.15 scores every fitting size by the area its wrapped block covers and takes
the largest fill, excluding only the "word list" look (3+ lines, nearly every one
a single word). A 15%-inset inscribed-rectangle fit is also kept as a floor for the
balloon-outline path, because the outline detection fragments easily.
"""
from PIL import Image, ImageDraw, ImageFont

import numpy as np

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _fit, _fit_common, _fit_shape


def _fp():
    return resolve_font_path(None)


def _extent(text, fitted):
    size, lines, font = fitted
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    sw = max(1, size // 8)
    bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=2,
                                 align="center", stroke_width=sw)
    return bb[2] - bb[0], bb[3] - bb[1]


def test_short_line_in_a_big_balloon_grows_to_the_cap():
    """'Ah' in a 133x138 bubble used to letter at 36px; it fits at the cap."""
    fp = _fp()
    if not fp:
        return
    size, lines, _f = _fit("Ah", 120, 124, fp, max_font=50)
    assert size >= 46, f"short line only reached {size}px"
    tw, th = _extent("Ah", (size, lines, _f))
    assert tw <= 120 and th <= 124


def test_long_line_still_fills_a_tall_balloon():
    """The 269x475 balloon that was lettered at 25px (9% fill)."""
    fp = _fp()
    if not fp:
        return
    text = "I'm sorry, big sister-"
    fitted = _fit(text, 242, 428, fp, max_font=50)
    assert fitted is not None
    size, lines, _f = fitted
    tw, th = _extent(text, fitted)
    assert size >= 30, f"balloon still lettered small: {size}px"
    assert (tw * th) / (242 * 428) >= 0.15, "lettering still barely fills the balloon"


def test_lettering_never_exceeds_its_box():
    """The invariant that must hold at every size: nothing spills out."""
    fp = _fp()
    if not fp:
        return
    cases = [
        ("Great fortune", 158, 251, 50),
        ("I lined up for two days and two nights...", 165, 290, 50),
        ("This is a marriage stone from the most efficacious Yue Lao Temple.", 267, 264, 50),
        ("This show is my life-what right do you have to stop it?!", 307, 300, 50),
        ("Marriage.", 152, 78, 50),
        ("Ah", 107, 112, 50),
    ]
    for text, w, h, cap in cases:
        fitted = _fit(text, w, h, fp, max_font=cap)
        assert fitted is not None, f"no fit for {text!r} in {w}x{h}"
        tw, th = _extent(text, fitted)
        assert tw <= w and th <= h, f"{text!r} overflowed {w}x{h} with {tw}x{th}"


def test_word_list_look_is_a_tie_break_not_a_veto():
    """v0.27.27. The stacked "word list" look is one to AVOID, but only when avoiding
    it is nearly free.

    It used to be a HARD exclusion, and because bigger letters wrap to fewer words per
    line it deleted precisely the largest candidates — leaving the fitter to maximise
    fill among the SMALL sizes. That is the "big balloon, tiny text" defect (measured:
    20% of blocks, mean +31%, worst +100%). The stack is now allowed whenever it is the
    larger size, and the chosen size must stay within one step of the maximum.
    """
    fp = _fp()
    if not fp:
        return
    text = "I'm sorry, big sister-"
    fitted = _fit(text, 150, 460, fp, max_font=50)
    assert fitted is not None
    size, lines, _f = fitted
    # the geometric maximum for this box, computed independently
    from PIL import Image, ImageDraw, ImageFont
    from app.pipeline.typeset import _wrap
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    ref = None
    for s in range(50, 7, -1):
        f = ImageFont.truetype(fp, s)
        ls = _wrap(probe, text, f, 150)
        bb = probe.multiline_textbbox((0, 0), "\n".join(ls), font=f, spacing=2,
                                      align="center", stroke_width=max(1, s // 8))
        if bb[2] - bb[0] <= 150 and bb[3] - bb[1] <= 460:
            ref = s
            break
    assert ref is not None
    assert size >= ref - 1, (
        f"the word-list veto is back: {size}px chosen where {ref}px fits (lines={lines})")


def test_a_long_word_never_overflows_the_region():
    """The size guarantee is a FLOOR, not a licence to spill: a word too wide for the
    region must force a smaller size (or no fit), never overflow."""
    fp = _fp()
    if not fp:
        return
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    got = _fit("Extraordinary", 90, 400, fp, max_font=35)
    if got is None:
        return  # nothing fits at any size; the caller's fallback handles it
    size, lines, font = got
    widest = max(
        probe.textbbox((0, 0), ln, font=font, stroke_width=max(1, size // 8))[2]
        for ln in lines
    )
    assert widest <= 92, f"a line {widest}px wide overflowed a 90px region at {size}px"


def test_two_word_block_may_stack():
    """'Great / fortune' is ordinary comic lettering, not a word list."""
    fp = _fp()
    if not fp:
        return
    fitted = _fit("Great fortune", 158, 251, fp, max_font=50)
    assert fitted is not None
    size, lines, _f = fitted
    assert size >= 24, f"two-word block lettered at {size}px"


def test_fragmented_balloon_outline_falls_back_to_the_inscribed_rect():
    """A broken outline detection must not shrink the lettering below the rectangle.

    `_region_avail` flood-fills the balloon's light interior; art or lettering
    inside the balloon splits it into slivers, and `avail` then reads 0 on most
    rows so every size above the sliver width is rejected. The inscribed-rectangle
    fit (15% inset) is the floor.
    """
    fp = _fp()
    if not fp:
        return
    # sliver pattern measured on job-1 page 7's 269x475 balloon
    avail = np.zeros(475, dtype=np.int32)
    for i in range(475):
        avail[i] = 234 if i % 40 < 12 else 0
    shape_only = _fit_shape("I'm sorry, big sister-", avail, 475, fp, max_font=50)
    assert shape_only is None or shape_only[0] < 30
    # the inscribed rectangle (15% inset, the pre-v0.27.8 behaviour) is the floor
    floor = _fit("I'm sorry, big sister-", int(269 * 0.7), int(475 * 0.7), fp, max_font=50)
    assert floor is not None
    assert (shape_only is None) or floor[0] > shape_only[0]
    assert floor[0] >= 20, f"rectangle floor lettered at {floor[0]}px"
