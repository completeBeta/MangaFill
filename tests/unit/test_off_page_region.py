"""v0.27.37 — a region that lies mostly OFF the page (job-2 p56).

MEASURED DEFECT (found by the v0.27.35 QA pass)
Job-2 page 56's caption box is `[78, 1564, 530, 117]` on a 1600px-tall page: y+h = 1681, so
only 36 of its 117 rows are on-page. The Korean was erased and the English was lettered into
the off-page part, so the reader got a black band with no translation. Erasing source text we
cannot then letter is worse than leaving it, because the erase is irreversible.
"""
from __future__ import annotations

import numpy as np

from app.pipeline.render import _erase_rects_checked, _region_on_page


def test_region_is_clamped_to_the_page():
    # job-2 p56's caption, on its real page size
    assert _region_on_page((78, 1564, 530, 117), 690, 1600) == (78, 1564, 530, 36)
    # a normal region is returned untouched
    assert _region_on_page((10, 20, 100, 200), 690, 1600) == (10, 20, 100, 200)
    # a box hanging off the left edge is clamped, not dropped
    assert _region_on_page((-40, 10, 130, 60), 690, 1600) == (0, 10, 90, 60)


def test_region_with_no_usable_page_area_is_dropped():
    # off the bottom by more than the usable minimum
    assert _region_on_page((300, 1620, 100, 60), 690, 1600) is None
    # off the right edge
    assert _region_on_page((700, 100, 80, 80), 690, 1600) is None
    # a sliver of a few rows cannot carry its English
    assert _region_on_page((78, 1595, 530, 117), 690, 1600) is None


def test_off_page_box_is_never_erased():
    """The erase is irreversible — never take source text we cannot letter back."""
    gray = np.full((1600, 690), 255, dtype=np.uint8)
    on_page = _erase_rects_checked(gray, (78, 1500, 100, 60), 690, 1600)
    assert on_page == [(78, 1500, 100, 60)], "an on-page small box keeps the plain rectangle"
    assert _erase_rects_checked(gray, (300, 1620, 100, 60), 690, 1600) == []
    assert _erase_rects_checked(gray, (700, 100, 80, 80), 690, 1600) == []
