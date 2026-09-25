"""v0.27.40 - narration drawn OVER artwork may widen over the art (class E).

MEASURED DEFECT (job-3 page 10, found by the readability pass on pages 9-12)
A 54x346 column of vertical narration lies across the character's hair, with artwork on
BOTH sides. `_caption_region` only grows into un-inked, unclaimed space, so with ink on
both sides it could not grow at all: the region stayed 54px wide and the fit collapsed to
8px — "I nearly died a few times, though..." came out as seven 8px lines beside dialogue
lettered at 22px, on the same page. The ORIGINAL lettering had the same problem and solved
it by drawing wider text over the art; our lettering carries its own outline, so it stays
legible there. The strip now retries the horizontal growth against OTHER TEXT BLOCKS ONLY
when the ink-blocked attempt yields nothing, under the same 2.5x cap.

Measured after the change: the region went 54px -> 158px and the block left the "under
15px" set; on pages 9-12 the count of tiny blocks fell from 7 to 3.
"""
from __future__ import annotations

import numpy as np

from app.pipeline.render import _caption_region


def _page(w=600, h=800, base=40):
    """A page that is all dark "artwork" (the source text was inked over it)."""
    return np.full((h, w), base, dtype=np.uint8)


def test_narration_over_artwork_widens_into_the_art():
    gray = _page()
    box = (100, 150, 54, 346)
    out = _caption_region(box, 600, 800, gray=gray, obstacles=[])
    assert out[2] > 54 * 2, f"expected the strip to widen over the art, got {out}"
    assert out[2] <= 54 * 2.5 + 24 + 1, f"and to stay inside the cap: {out}"


def test_a_neighbouring_text_block_still_blocks_the_widening():
    gray = _page()
    box = (300, 150, 54, 346)
    sib = (360, 150, 54, 346)          # the next column of text, right beside it
    out = _caption_region(box, 600, 800, gray=gray, obstacles=[sib])
    widened = out[2]
    # it may grow to the LEFT (free of the sibling) but must not run over the sibling's box
    assert out[0] + widened <= sib[0] + 1, (out, sib)


def test_a_short_wide_label_is_not_widened():
    gray = _page()
    box = (100, 150, 120, 40)
    out = _caption_region(box, 600, 800, gray=gray, obstacles=[])
    assert out[2] == 120, out
