"""v0.27.39 - the profile must be indexed from the BOX it is returned with.

MEASURED DEFECT (job-3 page 45, `Though I don't know if I'll come again next time...`)
`_region_avail` places its box around the balloon's AXIS, but returned the width profile
indexed from the interior's bbox top. Where those disagree the fitter is told that rows ABOVE
the balloon are as wide as the balloon's waist: p45's box top sat 15px above the interior's
first row, the profile reported 116px of width up there, a line went into those rows, and it
was drawn across the top arc (outside ink 21px -> 527px when this was attributed to the wrong
change; the enclosure was never the problem - its overhang is symmetric, i.e. one broad oval).

The invariant pinned here: rows of the returned profile that fall OUTSIDE the interior's own
rows must be 0, because no width was ever measured there.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest
from PIL import Image

from app.pipeline.typeset import _region_avail, balloon_from_original

BUNDLES = {
    # page stem -> the block that was measured, and the balloon's first interior row
    "00045": (443, 101),
    "00085": (242, 1059),
}


def _load(stem):
    p = f"/tmp/mf_replay/{stem}"
    if not os.path.exists(p + ".json"):
        pytest.skip(f"replay bundle for {stem} not captured on this host")
    b = json.load(open(p + ".json"))
    orig = np.asarray(Image.open(p + "_orig.png").convert("L"))
    inp = np.asarray(Image.open(p + "_inpaint.png").convert("L"))
    return b, orig, inp


@pytest.mark.parametrize("stem,box_xy", list(BUNDLES.items()))
def test_rows_outside_the_interior_have_no_width(stem, box_xy):
    b, orig, inp = _load(stem)
    blk = [x for x in b["blocks"]
           if abs(x["bbox"][0] - box_xy[0]) < 6 and abs(x["bbox"][1] - box_xy[1]) < 6]
    assert blk, f"block {box_xy} not found on page {stem}"
    blk = blk[0]
    box = tuple(blk["bbox"])
    region = tuple(b["regions"][str(blk["i"])])
    focus = (box[0] + box[2] // 2, box[1] + box[3] // 2)

    ball = balloon_from_original(orig, region, focus=focus, box=box)
    assert ball is not None
    mask, mbox = ball
    prof, ebox = _region_avail(inp, region, orig_gray=orig, focus=focus, box=box)
    assert prof is not None and ebox is not None

    # every row the returned box reaches beyond the interior must carry zero width
    interior_rows = range(mbox[1], mbox[1] + mask.shape[0])
    leading = max(0, min(int(mbox[1]), ebox[1] + len(prof)) - ebox[1])
    if leading:
        assert int(prof[:leading].max()) == 0, (
            f"{stem}: the box starts {leading}px above the interior's first row "
            f"({mbox[1]}) but the profile claims {int(prof[:leading].max())}px of width there")
    trailing = max(0, (ebox[1] + len(prof)) - (mbox[1] + mask.shape[0]))
    if trailing:
        assert int(prof[len(prof) - trailing:].max()) == 0, (
            f"{stem}: the box runs {trailing}px past the interior's last row but the profile "
            f"claims {int(prof[len(prof) - trailing:].max())}px there")
    # and the rows that ARE inside the interior carry real widths
    assert int(prof.max()) > 0
