"""v0.27.31 — lettering size + placement fix (job-3 page 7 vs the commercial release).

MEASURED DEFECT
On job-3 page 7 a 226x274 balloon carrying "This time he collapsed just from
lightly running around the yard..." was lettered at 12px with the block sitting
off-centre; the commercial (Ichigo) release of the same book letters the same
balloon at 18px, centred. Across the page's 14 balloons our ink inside the balloon
was 0.057 vs their 0.074 (0.77x) — the "lots of white space" complaint.

ROOT CAUSE (both halves)
  * SIZE: `_wrap` wrapped to the balloon's WIDEST row (`avail.max()`) while
    `_lines_fit_shape` then required every line to fit the NARROWEST row of its own
    band. On a tapered oval those two rules fight, so every size above a small one
    was rejected. Measured on a modelled balloon of the real geometry: 14px, where
    a wrap width chosen from the shape allows 16px.
  * PLACEMENT: the block was centred on its bounding box. An irregular outline (a
    tail, a squashed side) puts the bbox centre off the visual centre, so the ink
    sat low/high with all the slack on one side.

These tests pin the OBSERVABLE behaviour, not internal numbers.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.pipeline import typeset
from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _draw_box, _fit_common, _lines_fit_shape, _region_avail

# Use the app's OWN face resolution: the test must exercise the font the app ships,
# not one that happens to exist in the developer's checkout (the container image does
# not carry every face in `fonts/`).
FONT = resolve_font_path(None) or "fonts/AnimeAce-Bold.ttf"
LONG = "This time he collapsed just from lightly running around the yard..."
PAD = 70


def oval_page(w: int, h: int, tail: int = 0, tail_w: int = 26):
    """An 'inpainted page' whose lettering space is an oval, optionally with a tail.

    The tail matters: it is the real reason a balloon's bounding box is not its
    visual centre, and it is what the anchoring test exercises.
    """
    img = Image.new("RGB", (w + 2 * PAD, h + 2 * PAD + tail), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([PAD, PAD, PAD + w, PAD + h], fill=(255, 255, 255))
    if tail:
        cx = PAD + w // 2
        d.rectangle([cx - tail_w // 2, PAD + h - 4, cx + tail_w // 2, PAD + h + tail],
                    fill=(255, 255, 255))
    return img, (PAD, PAD, w, h + tail)


def fit_for(img, region, text=LONG, **kw):
    avail, box = _region_avail(np.array(img.convert("L")), region)
    res = _fit_common(text, int(avail.max()), box[3], FONT, max_font=35,
                      avail=avail, region_h=box[3], **kw)
    return avail, box, res


def ink_of(img, box, text=LONG, avail=None):
    out = img.copy()
    _draw_box(out, ImageDraw.Draw(out), box, text, FONT, 35, 0.0, avail)
    a = np.array(out.convert("L")).astype(np.int16)
    a0 = np.array(img.convert("L")).astype(np.int16)
    ink = (a < 128) & (a0 >= 128)
    ys, xs = np.nonzero(ink)
    return ink, ys, xs


def test_wrap_width_choice_makes_the_lettering_bigger(monkeypatch):
    """The fix's whole point: a wrap width chosen from the shape admits a bigger size."""
    img, region = oval_page(180, 274)

    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0,))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.0)
    _a, _b, strict = fit_for(img, region)

    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    _a2, _b2, fixed = fit_for(img, region)

    assert strict is not None and fixed is not None
    assert fixed[0] > strict[0], (
        f"wrap-width choice did not grow the lettering: {strict[0]}px -> {fixed[0]}px")


def test_size_is_preferred_over_a_taller_stack(monkeypatch):
    """SIZE FIRST: inside the guaranteed band the LARGEST size wins.

    A fill-maximising rule preferred a tall stack of short lines (measured on the
    modelled balloon: 15px over 10 lines where 16px over 8 fitted) — the 'word list'
    look earlier releases fought. The invariant asserted here is font-independent:
    the chosen size is never more than one step below the largest that fitted.
    """
    img, region = oval_page(180, 274)
    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    avail, box, res = fit_for(img, region)
    assert res is not None
    lf = typeset._last_fit
    assert lf.get("largest_fitting") is not None
    assert lf["largest_fitting"] - res[0] <= 1, (
        f"chosen {res[0]}px is more than one step below the largest that fitted "
        f"({lf['largest_fitting']}px)")


def test_lettering_never_crosses_the_outline(monkeypatch):
    """The acceptance criterion: bigger lettering must still stay inside the balloon."""
    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    for text in (LONG, "If you train, you can reach average stamina",
                 "According to what that person said"):
        img, region = oval_page(180, 274)
        avail, box, res = fit_for(img, region, text=text)
        assert res is not None
        ink, ys, xs = ink_of(img, box, text=text, avail=avail)
        cx = box[0] + box[2] / 2.0
        cy = box[1] + box[3] / 2.0
        outside = ((((xs - cx) ** 2) / (box[2] / 2.0) ** 2) +
                   (((ys - cy) ** 2) / (box[3] / 2.0) ** 2)) > 1.0
        frac = outside.sum() / max(1, ink.sum())
        assert frac < 0.02, (
            f"{frac:.1%} of the ink falls outside the balloon for {text!r} "
            f"(outline crossing)")


def test_box_is_centred_on_the_interiors_axis_not_its_bbox(monkeypatch):
    """An asymmetric interior must be lettered on the BALLOON's axis, not its bbox.

    Measured on job-3 page 7: the balloon's interior bbox centred on x=451 while every
    row of the balloon centred on x=468-470, so bbox-centred lettering sat ~18px left
    of the balloon and the user saw it as left-aligned. The reference letterer centres
    on the axis (x=469.5). Built here as an ellipse with a thin spur to the LEFT, so the
    bbox is dragged left while the balloon's own centre does not move.
    """
    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    pad = 60
    ex, ey, ew, eh = pad + 60, pad, 130, 220         # the balloon
    img = Image.new("RGB", (ew + 2 * pad + 60, eh + 2 * pad), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([ex, ey, ex + ew, ey + eh], fill=(255, 255, 255))
    d.rectangle([pad, ey + eh // 2 - 8, ex + 4, ey + eh // 2 + 8], fill=(255, 255, 255))
    region = (pad, pad, ew + 60, eh)

    got = _region_avail(np.array(img.convert("L")), region)
    assert got is not None
    avail, box = got
    balloon_centre = ex + ew / 2.0                   # the shape's true centre
    bbox_centre = (pad + (ex + ew)) / 2.0            # dragged left by the spur
    assert abs(balloon_centre - bbox_centre) > 12, "test shape is not asymmetric enough"

    box_centre = box[0] + box[2] / 2.0
    assert abs(box_centre - balloon_centre) < abs(box_centre - bbox_centre), (
        f"returned box centred at {box_centre:.1f}: not closer to the balloon centre "
        f"{balloon_centre:.1f} than to the bbox centre {bbox_centre:.1f}")


def test_ink_lands_where_the_box_puts_it(monkeypatch):
    """The drawn block sits on the box's axis — and inside the box.

    v0.27.37: the block may sit OFF the vertical centre, because a tapered balloon's
    profile is narrower at the top/bottom and `_placement_top` slides the block to the
    rows that are wide enough rather than shrinking it (the v0.27.34 trade that was
    rejected). The rules that must NOT move are the v0.27.32 ones: horizontally the ink
    is centred on the balloon's axis (which the returned box carries), and every line
    stays inside the box it was handed.
    """
    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    img, region = oval_page(180, 274)
    avail, box, res = fit_for(img, region)
    assert res is not None
    _ink, ys, xs = ink_of(img, box, avail=avail)
    ink_cx = (xs.min() + xs.max()) / 2.0
    ink_cy = (ys.min() + ys.max()) / 2.0
    assert abs(ink_cx - (box[0] + box[2] / 2.0)) <= 6, "ink is not horizontally centred"
    assert ys.min() >= box[1] - 2 and ys.max() <= box[1] + box[3] + 2, \
        "ink left the box the fitter was handed"
    top = typeset._last_fit.get("top")
    if top is None:
        assert abs(ink_cy - (box[1] + box[3] / 2.0)) <= 8, "ink is not vertically centred"
    else:
        assert abs(ys.min() - (box[1] + top)) <= 12, (
            f"the fitter chose top={top} but the ink starts at {ys.min() - box[1]} "
            f"— the placement was computed and then ignored")


def test_line_bands_are_clipped_to_the_profile_length():
    """regression: indexing `avail` past its end must not reject every candidate.

    `avail` has one entry per row of the balloon interior while the fitter is handed
    `region_h` from the region box; when the box is looser than the interior the two
    differ. An empty slice used to make `min()` raise (or read 0) and every
    candidate above a small size was rejected — a fit collapsing for no visible
    reason. The clamp means the last band reads the profile's last row instead.
    """
    avail = np.array([0] * 5 + [80] * 40, dtype=np.int32)   # 45 rows of profile
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = ImageFont.truetype(FONT, 12)
    # region_h (200) is far larger than len(avail) (45) — the mismatch that made a
    # fit collapse. Must not raise, and must not reject for the wrong reason.
    assert _lines_fit_shape(probe, ["hello"], font, avail, 200, 20) is True


@pytest.mark.parametrize("text", [LONG, "Hi", "According to what that person said"])
def test_fit_is_deterministic(text, monkeypatch):
    """Same input, same answer — a fit that changes run to run is undebuggable."""
    monkeypatch.setattr(typeset, "WRAP_FRACS", (1.0, 0.9, 0.8, 0.7, 0.6))
    monkeypatch.setattr(typeset, "SHAPE_TOL", 1.2)
    img, region = oval_page(180, 274)
    first = fit_for(img, region, text=text)[2]
    second = fit_for(img, region, text=text)[2]
    assert first[0] == second[0]
    assert first[1] == second[1]
