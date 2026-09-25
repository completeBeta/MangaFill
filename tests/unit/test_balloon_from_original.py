"""v0.27.37 — the balloon is measured on the ORIGINAL, and the block is PLACED, not shrunk.

MEASURED DEFECT (job-3 page 164, confirmed on the shipped build)
The block "By saying the activation key out loud…" was lettered with its first line drawn
ACROSS the balloon's top arc (~34px). Two causes, both on this page:
  * the erase mask runs over the strokes inside a block's box, and that box clips the
    balloon's outline — so the outline is GONE from the page the fitter measures, the
    flood walks out through the gap, and the "interior" comes back as the region box (a
    flat 206x205 profile on a ~460px balloon);
  * the container the text was sized to was a 205px pocket INSIDE that balloon (glyphs are
    walls to a flood on a page that still has its text), so the English sat in the
    balloon's top half at the wrong size.
The fix takes the balloon from the ORIGINAL (`balloon_from_original`: the light region its
closed outline encloses, holes filled) and then chooses WHERE the block goes inside it
(`_placement_top`) instead of stepping the size down — the trade v0.27.34 was withdrawn for.

These tests build that geometry synthetically: a closed outline, source glyphs inside it,
and an "inpaint" whose erase box has taken the outline's top away.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from app.pipeline import typeset
from app.pipeline.fonts import resolve_font_path
from app.pipeline.types import TextBlock
from app.pipeline.typeset import (balloon_from_original, _placement_top,
                                  _profile_from_mask, _region_avail, typeset_page)

TEXT = "By saying the activation key out loud, you can tell which skill is being used."


def _page(erase_top: bool = False):
    """(original, inpainted, region, block) for a balloon 220x340 with a clipped erase.

    The original is a dark page with a white oval balloon and dark glyph marks inside it.
    `erase_top` repaints the balloon's top cap white — exactly what the production erase
    mask does to job-3 p164 — so the inpainted page's outline has a hole in it.
    """
    W, H = 360, 470
    x0, y0, w, h = 70, 60, 220, 340
    orig = Image.new("RGB", (W, H), (110, 110, 110))
    d = ImageDraw.Draw(orig)
    d.ellipse([x0, y0, x0 + w, y0 + h], fill=(255, 255, 255), outline=(0, 0, 0), width=5)
    # source glyphs (dense enough to be a wall to a plain flood on the original)
    for r in range(y0 + 40, y0 + h - 30, 26):
        for c in range(x0 + 30, x0 + w - 30, 24):
            d.rectangle([c, r, c + 16, r + 18], fill=(0, 0, 0))
    inp = orig.copy()
    if erase_top:
        di = ImageDraw.Draw(inp)
        # From the page's LEFT EDGE across the balloon's top: the erase takes the arc AND
        # leaves a light corridor to the page border — how the flood escapes production's
        # balloon on job-3 p164.
        di.rectangle([0, y0 - 6, W, y0 + 34], fill=(255, 255, 255))
    region = (x0 - 16, y0 - 16, w + 32, h + 32)
    block = TextBlock(bbox=(x0 + 20, y0 + 40, 180, 150), text="x", translation=TEXT,
                      orientation="vertical", confidence=0.9)
    return orig, inp, region, block, (x0, y0, w, h)


def test_balloon_is_found_through_the_erase_gap():
    """The balloon the ORIGINAL encloses is recovered even though the inpaint has a hole."""
    orig, inp, region, block, (x0, y0, w, h) = _page(erase_top=True)
    got = balloon_from_original(np.asarray(orig.convert("L")), region,
                               focus=(block.bbox[0] + block.bbox[2] // 2,
                                      block.bbox[1] + block.bbox[3] // 2))
    assert got is not None, "the balloon was not found on the original page"
    mask, box = got
    assert mask.sum() > 0.5 * w * h, "the mask is a pocket, not the balloon"
    # the mask must cover the glyphs too (holes filled), or the profile would be measured
    # against one pocket between two columns of text
    gx = x0 + w // 2 - box[0]
    gy = y0 + h // 2 - box[1]
    assert mask[gy, gx], "the balloon mask does not cover the middle of the balloon"
    prof = _profile_from_mask(mask, np.asarray(orig.convert("L"))[box[1]:box[1] + box[3],
                                                                 box[0]:box[0] + box[2]])
    assert prof is not None
    avail = prof[0]
    top = int(avail[0])
    mid = int(avail[len(avail) // 2])
    assert mid > top, "the profile is not tapered — it is a rectangle, not the oval"


def test_open_outline_is_refused_not_guessed():
    """A genuinely bleeding balloon (its interior reaches the light background) ⇒ None."""
    W, H = 360, 470
    orig = Image.new("RGB", (W, H), (230, 230, 230))          # LIGHT page background
    d = ImageDraw.Draw(orig)
    d.ellipse([70, 60, 290, 400], fill=(255, 255, 255), outline=(0, 0, 0), width=5)
    d.rectangle([150, 55, 220, 70], fill=(255, 255, 255))
    #   ^ a hole in the outline that opens onto the light background, so the interior is
    #     reachable from the crop's border — the "bleeding balloon" case. A grey patch
    #     would NOT be an opening (the flood cannot pass through it either), and a hole
    #     into a dark dead end still leaves the interior properly enclosed, which is why
    #     the fixture has to be built this way.
    region = (54, 44, 252, 372)
    got = balloon_from_original(np.asarray(orig.convert("L")), region, focus=(180, 230))
    assert got is None, "an open outline must not be reported as a balloon"


def test_lettering_stays_inside_when_the_inpaint_took_the_outline():
    """The end-to-end invariant: with `page_gray` the ink cannot cross the arc.

    The fixture models production's geometry: the erase has whitened the balloon's top
    (arc included) into a band that reaches the page's edges, so a flood on the inpainted
    page claims that band as free space. The precondition is asserted directly — the
    un-clipped profile really does claim more width than the balloon has — and then the
    render is checked: with `page_gray` no lettering lands outside the oval.
    """
    fp = resolve_font_path(None)
    if not fp:
        return
    orig, inp, _region, block, (x0, y0, w, h) = _page(erase_top=True)
    # the container production's flood would have produced: the whitened band + the top
    # half of the balloon's interior
    region = (x0 - 16, y0 - 16, w + 32, h // 2)
    inside = np.asarray(orig.convert("L")) >= 200
    before = np.asarray(inp.convert("L")).astype(int)
    og = np.asarray(orig.convert("L"))
    ig = np.asarray(inp.convert("L"))
    focus = (block.bbox[0] + block.bbox[2] // 2, block.bbox[1] + block.bbox[3] // 2)

    no_clip = _region_avail(ig, region)
    clip = _region_avail(ig, region, orig_gray=og, focus=focus)
    assert no_clip is not None and clip is not None, "fixture: no profile to compare"
    assert int(no_clip[0].max()) > int(clip[0].max()), (
        "fixture does not reproduce the defect — the clip removed nothing, so the flood "
        "never claimed the erased band in the first place")

    def ink_of(**kw):
        out = typeset_page(inp, [block], font_path=fp, regions={id(block): region},
                           only={id(block)}, shapes={id(block)},
                           page_label="synthetic.jpg", **kw)
        after = np.asarray(out.convert("L")).astype(int)
        return (after < before - 40) & (after < 120)

    fixed = ink_of(page_gray=og)
    assert fixed.sum() > 50, "no lettering was drawn"
    assert (fixed & ~inside).sum() == 0, (
        f"{(fixed & ~inside).sum()}px of lettering still lands outside the balloon")


def test_placement_slides_instead_of_shrinking():
    """`_placement_top` keeps a line that fits only away from the centred position."""
    # a 50-tall block only fits inside the 60-row wide band
    avail = np.array([0] * 40 + [60] * 60 + [0] * 100, dtype=np.int32)
    rows = [(0, 50)]                       # one line, 50 rows tall
    centred = max(0, (len(avail) - 50) // 2)
    assert int(avail[centred:centred + 50].min()) == 0, "fixture: centring must fail"
    one = _placement_top(rows, [50], avail, len(avail), tol=1.0)
    assert one is not None and one != centred, (
        "a block that only fits the wide band must be slid there")
    assert int(avail[one:one + 50].min()) >= 50
    # nothing fits if the line is wider than the whole profile
    assert _placement_top(rows, [999], avail, len(avail), tol=1.0) is None
    # and a narrow block still cannot be placed where the profile is empty (avail = 0)
    # — it lands at the last offset inside the wide band, closest to the centre
    assert _placement_top([(0, 30)], [20], avail, len(avail), tol=1.0) == 70


def test_profile_is_aligned_to_the_box_it_returns():
    """The profile is indexed by the BOX's rows, not the mask's (the dy shift)."""
    mask = np.zeros((100, 60), dtype=bool)
    mask[10:90, 10:50] = True                      # an interior low in its box
    crop = np.full((100, 60), 255, dtype=np.uint8)
    got = _profile_from_mask(mask, crop)
    assert got is not None
    avail, (dx, dy) = got
    assert len(avail) == 100
    # the wide band must sit where the block will be drawn: rows [dy+10, dy+90)
    top = int(round(dy)) + 10
    assert avail[max(0, top):top + 4].min() > 0, "profile rows are not aligned to the box"
    assert int(avail[0]) == 0 and int(avail[-1]) == 0, "the shifted ends must read 0"
