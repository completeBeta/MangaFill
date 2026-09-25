"""v0.27.38 - an enclosure that is really a NEIGHBOUR must not size the lettering.

MEASURED DEFECT (job-3 page 85, caught by the user from a screenshot after v0.27.37 had been
reported fixed off a crop that cut the first line)
`Even though I took the alcohol breakdown medicine Hazal gave me earlier-` was fitted to
**274px** of "available" width for a **156px** block, and its first line was drawn across the
wall into the neighbouring balloon (`Huh? That's strange.`). The two balloons touch, and where
they meet their light interiors are ONE connected region: the distance transform is a single
component at EVERY threshold up to 60px, so there is no neck to cut - and the flood on the
inpainted page merges the same way (274px).

The tell is geometry, not topology: a neighbour hangs off ONE side of the block's own box. A
broad oval whose source text is a narrow COLUMN also exceeds 1.5x the box, but on BOTH sides -
bounding those left 68 blocks worse against 260 improved in the first volume gate, which is why
the one-sided test is in the guard and is pinned here.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline.typeset import balloon_from_original, _region_avail


def _page(bal, marks):
    im = Image.new("RGB", (420, 420), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse(bal, outline=(0, 0, 0), width=9)
    for m in marks:
        d.rectangle(m, fill=(0, 0, 0))
    return np.asarray(im.convert("L"))


def _marks(x0, y0, cols, rows):
    return [(x0 + 26 * i, y0 + 28 * j, x0 + 26 * i + 14, y0 + 28 * j + 18)
            for i in range(cols) for j in range(rows)]


BUNDLE = "/tmp/mf_replay/00085"


@pytest.mark.skipif(not os.path.exists(BUNDLE + ".json"),
                    reason="replay bundle for job-3 page 85 not captured on this host")
def test_the_measured_page_85_enclosure_is_bounded():
    """The defect itself, on the captured page: 274px of claimed width -> 180px.

    A hand-drawn two-balloon fixture was tried first and dropped: sealing two crossing arcs
    against a 1px light leak, then needing enough crop growth for a large oval, makes the
    fixture a test of PIL's rasteriser rather than of this guard. The captured bundle IS the
    geometry, so the invariant is pinned on it (skipped where no bundle exists — see the
    manga-fill skill's replay harness).
    """
    bundle = json.load(open(BUNDLE + ".json"))
    orig = np.asarray(Image.open(BUNDLE + "_orig.png").convert("L"))
    inp = np.asarray(Image.open(BUNDLE + "_inpaint.png").convert("L"))
    reg = bundle["regions"]["11"]
    blk = bundle["blocks"][11]
    assert "alcohol breakdown" in blk["en"]
    box = tuple(blk["bbox"])
    region = tuple(reg)
    focus = (box[0] + box[2] // 2, box[1] + box[3] // 2)

    flood = _region_avail(inp, region)
    clipped = _region_avail(inp, region, orig_gray=orig, focus=focus, box=box)
    assert flood is not None and clipped is not None
    assert int(flood[0].max()) > 250, flood[0].max()      # the union: 274px for a 156px box
    assert int(clipped[0].max()) <= 200, clipped[0].max()  # bounded to the block's own balloon
    assert int(clipped[0].max()) < int(flood[0].max())


def test_a_wide_balloon_with_centred_text_is_left_alone():
    """The false-positive guard: a symmetric overhang is a broad balloon, not a neighbour."""
    orig = _page((40, 60, 380, 360), _marks(170, 190, 2, 2))
    box = (166, 182, 80, 60)
    region = (162, 178, 88, 68)
    focus = (box[0] + box[2] // 2, box[1] + box[3] // 2)
    without = balloon_from_original(orig, region, focus=focus)
    with_box = balloon_from_original(orig, region, focus=focus, box=box)
    assert without is not None and with_box is not None
    assert without[1][2] == with_box[1][2], (without[1], with_box[1])
    inp = np.full_like(orig, 255)
    a = _region_avail(inp, region, orig_gray=orig, focus=focus)
    b = _region_avail(inp, region, orig_gray=orig, focus=focus, box=box)
    assert a is not None and b is not None
    assert int(a[0].max()) == int(b[0].max())
