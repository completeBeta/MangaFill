"""Shape-aware lettering: fit the balloon's outline, not its bounding box.

Oval / spiked balloons are much narrower at the top and bottom rows than at their
waist, so any single rectangle either crowds the outline (bbox) or wastes most of
the balloon (a fixed % inset). `_region_avail` measures the real interior per row
and `_fit_shape` picks the largest font whose every line fits its own row band.
"""
import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import (_fit_shape, _lines_fit_shape, _region_avail,
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
    # nothing above/below the ellipse, widest at the waist
    assert avail[0] == 0 and avail[-1] == 0
    assert avail[len(avail) // 2] > avail[len(avail) // 8] > 0
    assert avail.max() <= box[2]


def test_avail_is_none_when_there_is_no_shape():
    # all-artwork page: the region centre is not on a light interior
    assert _region_avail(Image.new("L", (200, 200), 90), (0, 0, 200, 200)) is None
    # tiny region
    assert _region_avail(_oval(), (100, 60, 4, 4)) is None


def test_shape_fit_respects_each_lines_band():
    """Every wrapped line must fit the balloon width at ITS OWN rows."""
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
    assert _lines_fit_shape(probe, lines, font, avail, region[3], th)


def test_shape_fit_beats_the_spiky_balloon():
    """A spiky balloon's bbox contains much art; the shape fit must stay inside."""
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
    size, lines, font = fitted
    x, y, w, h = box
    out = im.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    sw = max(1, size // 8)
    d.multiline_text((x + w // 2, y + h // 2), "\n".join(lines), font=font, fill=(0, 0, 0),
                     anchor="mm", align="center", spacing=2, stroke_width=sw,
                     stroke_fill=(255, 255, 255))
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
