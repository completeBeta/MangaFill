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


def _old_fit(text: str, max_w: int, max_h: int, cap: int):
    """The v0.27.14 rule: largest size that fits with at most one lone-word line."""
    from app.pipeline.typeset import _wrap
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    best = clean = None
    for size in range(cap, 7, -1):
        font = ImageFont.truetype(_fp(), size)
        sw = max(1, size // 8)
        lines = _wrap(probe, text, font, max_w)
        bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=2,
                                     align="center", stroke_width=sw)
        if bb[2] - bb[0] > max_w or bb[3] - bb[1] > max_h:
            continue
        if best is None:
            best = (size, lines, font)
        if sum(1 for ln in lines if len(ln.split()) == 1) <= 1:
            clean = (size, lines, font)
            break
    return clean or best


def test_rect_path_fills_its_strip():
    """A boundary-less region must FILL its strip (v0.27.15) without spilling out.

    v0.27.8 kept the aesthetic "no lone-word line" sizing here, which is what left
    boxes 70-90% empty; the fill rule now sizes to the strip and only rejects the
    word-list look. The strip is derived from the source text's own box, so filling
    it cannot collide with a neighbouring panel the way v0.27.7's page-wide cap did.
    """
    fp = _fp()
    if not fp:
        return
    text = "Somewhere brilliant in the middle, though"
    old = _old_fit(text, 150, 240, 35)
    size, lines, _f = _fit(text, 150, 240, fp, max_font=35)
    assert old is not None and size > old[0], f"no gain on the old rule ({old[0]}px -> {size}px)"
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=_f, spacing=2,
                                 align="center", stroke_width=max(1, size // 8))
    assert bb[2] - bb[0] <= 150 and bb[3] - bb[1] <= 240
    if len(lines) >= 3:
        lone = sum(1 for ln in lines if len(ln.split()) == 1)
        assert lone < len(lines) - 1, f"lettered as a word list: {lines}"


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
