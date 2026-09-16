"""Lettering must fill the space it is given (v0.27.7 / v0.27.8).

v0.27.7 taught `_fit` to prefer filling a region, which fixed small lettering in
big balloons but blew text up in regions with no boundary to respect (caption
strips over artwork, floating mutter text) — it collided with neighbouring panels.

v0.27.8 splits the two cases:
  * a speech balloon is fitted to its ACTUAL OUTLINE (`_fit_shape`) — filled to
    the curve, never overrun;
  * a bare rectangle keeps the aesthetic "no lone-word line" sizing.
"""
from PIL import Image, ImageDraw, ImageFont

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _fit, _fit_shape, _region_avail


def _fp():
    return resolve_font_path(None)


def _oval_page(w=400, h=400, box=(100, 60, 300, 340)):
    """Grey 'artwork' page with one white balloon (black outline)."""
    im = Image.new("L", (w, h), 120)
    d = ImageDraw.Draw(im)
    d.ellipse(list(box), fill=255, outline=0, width=4)
    return im


def test_balloon_is_filled_to_its_curve():
    """A tall balloon is lettered larger than the old inset rectangle allowed."""
    fp = _fp()
    if not fp:
        return
    im = _oval_page()
    region = (100, 60, 200, 280)
    shape = _region_avail(im, region)
    assert shape is not None, "oval interior not detected"
    avail, _box = shape
    fitted = _fit_shape("Twelve years ago, Munakata Kyudo Dojo", avail, len(avail),
                        fp, max_font=40)
    assert fitted is not None
    size, lines, _f = fitted
    # the rectangle path (15% inset, the shape blind fit) is smaller here
    inset = max(int(min(region[2], region[3]) * 0.15), 6)
    rect = _fit("Twelve years ago, Munakata Kyudo Dojo",
                region[2] - 2 * inset, region[3] - 2 * inset, fp, max_font=40)
    assert rect is not None
    assert size > rect[0], f"shape fit {size}px did not beat the rectangle {rect[0]}px"
    assert size >= 12


def test_rect_path_keeps_aesthetic_sizing():
    """A boundary-less region must not blow a caption up past its own scale."""
    fp = _fp()
    if not fp:
        return
    # narrow strip, text that could be lettered as one word per line at a big size
    size, lines, _f = _fit("Somewhere brilliant in the middle, though", 150, 240, fp,
                           max_font=35)
    assert size < 35, f"caption blew up to {size}px in a boundary-less strip"
    assert sum(1 for ln in lines if len(ln.split()) == 1) <= 1


def test_roomy_box_behaviour_is_unchanged():
    fp = _fp()
    if not fp:
        return
    size, _l, _f = _fit("Hello there.", 300, 200, fp, max_font=35)
    assert size == 35, f"roomy box changed size to {size}px"


def test_shape_fit_keeps_glyphs_inside_the_outline():
    """The invariant the whole change exists for: no lettering outside the oval."""
    fp = _fp()
    if not fp:
        return
    im = _oval_page()
    region = (100, 60, 200, 280)
    shape = _region_avail(im, region)
    assert shape is not None
    avail, box = shape
    text = "Then I'll hold back no longer, not ever again"
    fitted = _fit_shape(text, avail, len(avail), fp, max_font=40)
    assert fitted is not None
    size, lines, font = fitted
    x, y, w, h = box
    out = im.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    sw = max(1, size // 8)
    d.multiline_text((x + w // 2, y + h // 2), "\n".join(lines), font=font, fill=(0, 0, 0),
                     anchor="mm", align="center", spacing=2, stroke_width=sw,
                     stroke_fill=(255, 255, 255))
    import numpy as np
    before = np.asarray(im.convert("L")).astype(int)
    after = np.asarray(out.convert("L")).astype(int)
    drawn = abs(before - after) > 40
    inside = np.asarray(im) >= 200          # balloon interior (no glyphs on it yet)
    assert drawn.sum() > 100, "nothing was drawn"
    assert not (drawn & ~inside).any(), "lettering escaped the balloon outline"
