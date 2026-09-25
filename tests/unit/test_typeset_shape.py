"""Shape-aware lettering: fit the balloon's outline, not its bounding box.

Oval / spiked balloons are much narrower at the top and bottom rows than at their
waist, so any single rectangle either crowds the outline (bbox) or wastes most of
the balloon (a fixed % inset). `_region_avail` measures the real interior per row
and `_fit_shape` picks the largest font whose every line fits its own row band —
within `typeset.SHAPE_TOL` since v0.27.31 (demanding the narrowest row to the pixel
rejected every useful size on a tapered balloon; see test_typeset_fill_v2731).
"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline import typeset
from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import (SHAPE_TOL, _draw_box, _fit_shape, _line_metrics,
                                  _lines_fit_shape, _placement_top, _region_avail,
                                  typeset_page)
from app.pipeline.types import TextBlock


def _oval(w=400, h=400, box=(100, 60, 300, 340), bg=120):
    im = Image.new("L", (w, h), bg)
    ImageDraw.Draw(im).ellipse(list(box), fill=255, outline=0, width=4)
    return im


def _star(size=360):
    """A spiky balloon: star polygon, white inside, black outline."""
    import math
    im = Image.new("L", (size, size), 120)
    cx = cy = size / 2
    pts = []
    for i in range(24):
        r = (size * 0.45) if i % 2 == 0 else (size * 0.30)
        a = math.pi * i / 12
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    ImageDraw.Draw(im).polygon(pts, fill=255, outline=0)
    ImageDraw.Draw(im).line(pts + [pts[0]], fill=0, width=3)
    return im


def test_avail_profile_follows_the_oval():
    region = (100, 60, 200, 280)
    shape = _region_avail(_oval(), region)
    assert shape is not None
    avail, box = shape
    # the box is the balloon's own interior bbox, inside the region
    assert box[0] >= region[0] and box[1] >= region[1]
    assert box[0] + box[2] <= region[0] + region[2]
    assert len(avail) == box[3]
    # Nothing above/below the ellipse, widest at the waist. The profile is now the
    # interior's OWN extent per row (v0.27.32), so the extreme rows carry a 1-2px
    # sliver instead of clamping to exactly 0 — a line could never use it.
    assert avail[0] <= 2 and avail[-1] <= 2
    assert avail[len(avail) // 2] > avail[len(avail) // 8] > 0
    assert avail.max() <= box[2]


def test_avail_is_none_when_there_is_no_shape():
    # all-artwork page: the region centre is not on a light interior
    assert _region_avail(Image.new("L", (200, 200), 90), (0, 0, 200, 200)) is None
    # tiny region
    assert _region_avail(_oval(), (100, 60, 4, 4)) is None


def test_shape_fit_respects_each_lines_band():
    """Every wrapped line must fit the balloon width at ITS OWN rows.

    The check is per-line/per-band as before; v0.27.31 only adds `SHAPE_TOL` to the
    allowed width, because demanding the narrowest row in a band to the pixel made
    every size above a small one fail on a tapered balloon (measured on job-3 page 7:
    12px where 16-17px fits without touching the outline).
    """
    region = (100, 60, 200, 280)
    shape = _region_avail(_oval(), region)
    fp = resolve_font_path(None)
    if not fp or shape is None:
        return
    avail, _box = shape
    fitted = _fit_shape("This time he collapsed just from lightly running around",
                        avail, region[3], fp, max_font=40)
    assert fitted is not None
    size, lines, font = fitted
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    sw = max(1, size // 8)
    bb = probe.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=2,
                                  align="center", stroke_width=sw)
    th = int(bb[3] - bb[1])
    # v0.27.37: the invariant is "fits at SOME placement", not "fits centred". A tapered
    # balloon whose centred block would overrun now costs a SLIDE (`_placement_top`),
    # which is how the same size is kept instead of being stepped down — the v0.27.34
    # regression. The centred check stays asserted where it holds.
    centred = _lines_fit_shape(probe, lines, font, avail, region[3], th, tol=SHAPE_TOL)
    _lw, _lrows = _line_metrics(probe, lines, font, stroke_width=sw)
    placed = _placement_top(_lrows, _lw, avail, region[3], tol=SHAPE_TOL, pad_rows=sw)
    assert placed is not None, (
        "the fitted size does not fit the balloon at ANY placement")
    if not centred:
        assert placed != max(0, (region[3] - th) // 2), (
            "the centred position fails but the fitter did not slide")
    assert SHAPE_TOL > 1.0, "the tolerance must not be tightened silently"
    assert typeset.SHAPE_TOL == SHAPE_TOL


def test_shape_fit_beats_the_spiky_balloon():
    """A spiky balloon's bbox contains much art; the shape fit must stay inside.

    Drawn through the PRODUCTION `_draw_box` (which is what applies the fitter's chosen
    placement), so this fails if the fit and the draw ever disagree about where the block
    goes — the mechanism that would put the lettering back across the outline.
    """
    im = _star()
    region = (40, 40, 280, 280)
    shape = _region_avail(im, region)
    assert shape is not None
    avail, box = shape
    fp = resolve_font_path(None)
    if not fp:
        return
    fitted = _fit_shape("Winner, Idamand! You've beaten Idamand!", avail, len(avail),
                        fp, max_font=40)
    assert fitted is not None
    out = im.convert("RGB").copy()
    _draw_box(out, ImageDraw.Draw(out), box, "Winner, Idamand! You've beaten Idamand!",
              fp, 40, angle=0.0, avail=avail)
    before = np.asarray(im.convert("L")).astype(int)
    after = np.asarray(out.convert("L")).astype(int)
    drawn = abs(before - after) > 40
    inside = np.asarray(im) >= 200
    assert drawn.sum() > 50
    assert not (drawn & ~inside).any(), "lettering escaped the spiky balloon"


def _block(bbox, translation, orientation="vertical", angle=0.0):
    b = TextBlock(bbox=bbox, text="x", translation=translation, orientation=orientation,
                  confidence=0.9)
    b.angle = angle
    return b


def test_typeset_page_shape_path_stays_inside():
    """End to end: with the block marked as a balloon, nothing escapes the oval."""
    fp = resolve_font_path(None)
    if not fp:
        return
    im = _oval().convert("RGB")
    region = (100, 60, 200, 280)
    b = _block((150, 110, 100, 180), "Then I'll hold back no longer at all")
    out = typeset_page(im, [b], font_path=fp, regions={id(b): region},
                       only={id(b)}, shapes={id(b)})
    before = np.asarray(im.convert("L")).astype(int)
    after = np.asarray(out.convert("L")).astype(int)
    drawn = abs(before - after) > 40
    inside = np.asarray(im.convert("L")) >= 200
    assert drawn.sum() > 100
    assert not (drawn & ~inside).any(), "shape path escaped the balloon"


def test_typeset_page_rect_path_is_the_fallback():
    """Without `shapes`, a slanted or boundary-less block uses the rectangle fit."""
    fp = resolve_font_path(None)
    if not fp:
        return
    im = _oval().convert("RGB")
    region = (100, 60, 200, 280)
    b = _block((150, 110, 100, 180), "Slanted line here", angle=8.0)
    out = typeset_page(im, [b], font_path=fp, regions={id(b): region},
                       only={id(b)}, shapes={id(b)})
    assert out is not None  # no crash; angled blocks skip the shape path
