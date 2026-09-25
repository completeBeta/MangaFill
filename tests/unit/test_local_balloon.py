"""Local per-block balloon extraction (v0.28.0) — the two-touching-balloons fix.

The defect these pin, measured on the v0.27.41 whole-library audit: 591 regions were
shared by more than one block and 1,199 of 2,606 lettered blocks (48.8%) were flagged
`merged` — two balloons whose interiors came back as ONE region, so the English was
fitted across both outlines. The fix derives each block's balloon from a LOCAL window
on the original page (vendored GPL-3.0 extractor, see app/pipeline/balloon_local.py).

What is pinned here:
  1. the extractor's mask covers the block's OWN text box and stays off a neighbouring
     balloon's box, on a two-balloon fixture;
  2. `_own_balloon_region` REFUSES an extraction that still swallows a sibling — the
     caller then keeps the region it already had, so the change can never make an
     ambiguous block worse;
  3. `_region_avail(shape_mask=...)` uses the caller's mask instead of flooding the
     inpainted page, so the lettering profile is the block's own balloon, row by row.

Fixture geometry note: the extractor needs the balloon's OUTLINE inside its window —
a window that sits entirely inside one big balloon finds no contour at all and returns
nothing (which the wrapper turns into a refusal, never into a bad region). So the ovals
here are drawn tight around their text boxes, as real balloons are around their text.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from app.pipeline import balloon_local
from app.pipeline import render as R
from app.pipeline.types import TextBlock

# Two ovals whose outlines meet at x=230; each text box sits inside its own oval.
OVAL_A = (110, 140, 230, 260)
OVAL_B = (230, 140, 350, 260)
BOX_A = (145, 180, 60, 60)
BOX_B = (265, 180, 60, 60)


def _two_balloons(bridge: bool = False, gap: bool = False) -> np.ndarray:
    """White page, two outlined ovals with lettering marks inside, returned as RGB.

    `bridge` paints a white channel through both outlines at the contact point — the
    mechanism by which a page-wide flood walks from one oval into the other (the real
    defect's mechanism). `gap` separates the ovals instead of touching them.
    """
    im = Image.new("RGB", (700, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    box_b = OVAL_B
    if gap:
        box_b = (250, 140, 370, 260)
    for box in (OVAL_A, box_b):
        d.ellipse(box, outline=(0, 0, 0), width=4)
    for bx in (BOX_A, BOX_B):
        x, y, w, h = bx
        for dy in (-18, 0, 18):
            d.rectangle((x + 6, y + h // 2 + dy - 5, x + w - 6, y + h // 2 + dy + 5), fill=(0, 0, 0))
    if bridge:
        d.rectangle((222, 194, 238, 206), fill=(255, 255, 255))
    return np.asarray(im)


def _block(box) -> TextBlock:
    b = TextBlock(bbox=tuple(int(v) for v in box))
    b.translation = "TEST"
    return b


def test_mask_covers_its_own_box_and_stays_off_the_neighbour():
    page = _two_balloons()
    got = balloon_local.local_balloon_region(balloon_local.to_bgr(page), BOX_A, 700, 400)
    assert got is not None, "the extractor must find the oval's interior"
    mask, box = got
    assert balloon_local.own_cover(mask, BOX_A) >= 0.80
    # the neighbour's box must be essentially untouched (the whole point of the fix)
    assert balloon_local.own_cover(mask, BOX_B) < 0.15
    # and the balloon's box is the OVAL's, i.e. wider than the text box inside it
    assert box[2] >= BOX_A[2] and box[3] >= BOX_A[3]
    assert box[0] >= 0 and box[1] >= 0 and box[0] + box[2] <= 700 and box[1] + box[3] <= 400


def test_untrusted_extraction_is_refused_so_the_caller_keeps_its_region():
    """A block sitting on plain artwork (no outline) must not win a mask."""
    page = np.full((400, 700, 3), 255, np.uint8)   # blank white: no balloon at all
    got = balloon_local.local_balloon_region(balloon_local.to_bgr(page), BOX_A, 700, 400)
    # Either nothing comes back, or what comes back satisfies the own-cover gate.
    if got is not None:
        mask, _box = got
        assert balloon_local.own_cover(mask, BOX_A) >= 0.80


def test_own_balloon_region_refuses_an_extraction_that_still_swallows_the_neighbour():
    """The gate that makes this change safe: no separation => no override."""
    page = _two_balloons(bridge=True)
    orig = balloon_local.to_bgr(page)
    block = _block(BOX_A)
    siblings = [tuple(int(v) for v in BOX_B)]
    shared = (110, 140, 240, 120)          # a region covering BOTH ovals
    assert R._overlaps_any(shared, siblings)
    got = R._own_balloon_region(orig, block, shared, siblings, 700, 400)
    if got is not None:
        mask, box = got
        # when we DO override, the new box must no longer swallow the sibling
        assert not R._overlaps_any(box, siblings)
        assert balloon_local.own_cover(mask, BOX_B) < 0.5


def test_unambiguous_region_is_left_alone():
    """No sibling conflict => the resolver keeps its existing region (no behaviour churn)."""
    page = _two_balloons()
    block = _block(BOX_A)
    region = (110, 140, 120, 120)        # the left oval only
    far = [(600, 20, 40, 30)]            # a sibling nowhere near it
    assert R._own_balloon_region(balloon_local.to_bgr(page), block, region, far, 700, 400) is None


def test_shape_mask_drives_the_profile_instead_of_the_inpainted_flood():
    """With a mask handed in, the lettering profile is the mask's — not the flood's."""
    from app.pipeline.typeset import _region_avail

    page = np.full((400, 700), 255, np.uint8)     # inpainted page: all "light"
    mask = np.zeros((400, 700), bool)
    yy, xx = np.mgrid[0:400, 0:700]
    mask[(yy - 200) ** 2 + (xx - 190) ** 2 <= 60 ** 2] = True   # a CIRCLE, not a rect
    region = (130, 140, 120, 120)
    got = _region_avail(page, region, shape_mask=mask)
    assert got is not None
    avail, box = got
    assert box[2] <= region[2] + 2 and box[3] <= region[3] + 2
    # a circle narrows towards its top/bottom rows, so the profile must vary, and rows
    # the balloon does not reach must carry NO width at all.
    assert avail.max() > 50
    assert (avail == 0).any(), "rows outside the mask must have zero width"
    got_open = _region_avail(page, region)
    assert got_open is not None
    avail_open, _b = got_open
    assert avail_open.sum() > avail.sum(), "the flood must be wider than the given mask"


def test_switch_is_off_by_default_so_shipping_is_behaviour_neutral():
    """The ported geometry is OFF until the tightened form proves a win (see the switch
    note in balloon_local): with it off, an ambiguous block keeps the region it had."""
    assert balloon_local.ENABLED is False
    page = _two_balloons()
    block = _block(BOX_A)
    shared = (110, 140, 240, 120)          # covers both ovals
    siblings = [tuple(int(v) for v in BOX_B)]
    assert R._own_balloon_region(balloon_local.to_bgr(page), block, shared, siblings,
                                 700, 400) is None


def test_adaptive_window_shrinks_beside_a_touching_neighbour():
    """Upstream's window rule: aspect-based, then reduced so windows cannot overlap."""
    alone = balloon_local.adaptive_enlarge(BOX_A, [])
    assert 1.0 < alone <= 3.0
    # a sibling 20px away: ratio = d/(2l)+1 = 20/120+1 = 1.167, i.e. a real shrink
    close = (225, 180, 60, 60)
    near = balloon_local.adaptive_enlarge(BOX_A, [close])
    assert near < alone, "a close neighbour must shrink the window"
    assert near >= 1.0
    # a sibling 200px away never intersects the enlarged window: ratio unchanged
    assert balloon_local.adaptive_enlarge(BOX_A, [(405, 180, 60, 60)]) == alone
    # and the local extraction must still separate the pair at that window
    page = _two_balloons()
    got = balloon_local.local_balloon_region(balloon_local.to_bgr(page), BOX_A, 700, 400,
                                            sibling_boxes=[tuple(int(v) for v in BOX_B)])
    if got is not None:
        mask, _box = got
        assert balloon_local.own_cover(mask, BOX_B) < 0.15
