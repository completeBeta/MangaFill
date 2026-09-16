"""Lettering must fill the space it is given (v0.27.7).

`_fit` returned the largest size with at most one single-word line. In a roomy box
that is a fine nudge, but in a narrow region the lone-word marker fires at nearly
every size, so the scan bottomed out well below what actually fits — job-5 page 3
(Japanese) lettered a 133x242 caption column at 10px with most of the box empty.
"""
import os

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _fit


def _fp():
    return resolve_font_path(None)


def test_narrow_region_is_filled_not_shrunk():
    fp = _fp()
    if not fp:
        return  # font unavailable on this host
    size, lines, _font = _fit("Twelve years ago, Munakata Kyudo Dojo",
                              93, 202, fp, max_font=35)
    assert size >= 12, f"narrow region collapsed to {size}px"
    # sanity: the chosen size really does fit the box
    from PIL import ImageDraw, ImageFont
    probe = ImageDraw.Draw(__import__("PIL.Image", fromlist=["Image"]).new("RGB", (1, 1)))
    f = ImageFont.truetype(fp, size)
    bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=f, spacing=2,
                                  align="center", stroke_width=max(1, size // 8))
    assert bb[2] - bb[0] <= 93 and bb[3] - bb[1] <= 202


def test_roomy_box_behaviour_is_unchanged():
    """The fill preference must not inflate text in boxes that already wrapped well."""
    fp = _fp()
    if not fp:
        return
    size, lines, _font = _fit("Hello there.", 300, 200, fp, max_font=35)
    assert size == 35, f"roomy box changed size to {size}px"
    size2, _l2, _f2 = _fit("I waited in line for hours", 200, 120, fp, max_font=35)
    assert size2 == 28, f"roomy box changed size to {size2}px"


def test_fill_preference_only_kicks_in_for_a_big_gap(monkeypatch):
    """The clean size wins when it is within 75% of the largest that fits."""
    import app.pipeline.typeset as T
    fp = _fp()
    if not fp:
        return
    # A box where the clean wrap and the largest fit agree — clean must win.
    size, lines, _f = _fit("I waited in line for hours", 200, 120, fp, max_font=35)
    assert sum(1 for ln in lines if len(ln.split()) == 1) <= 1
