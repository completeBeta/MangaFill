"""Boundary/carryover + free-text region rules (2026-09-15 job-2/job-4 defects).

Defect 1 — job-2 page 41: page 40's carryover patch overlapped page 41's own
bubble and stamped page 41's un-erased Korean back over its fresh English
("I'M RE하고"). Fixed by (a) erasing next-page text that the lookahead strip
pulled in, and (b) dropping carryover patches that overlap text this page owns.

Defect 2 — job-4 page 5: a tall-narrow vertical Japanese column (`根性はあるけど
器用貧乏！？`) had long English fitted into its ~1-glyph width, producing
microscopic lettering. Fixed with the same rule the ko/zh path already used.
"""
from __future__ import annotations

import numpy as np

from app.pipeline.render import (
    _drop_spurious_carryover,
    _extract_carryover,
    _free_text_region,
    _intersects,
    _patch_is_blank,
)
from app.pipeline.types import TextBlock


def _blk(bbox):
    return TextBlock(bbox=bbox, text="x", confidence=None, orientation="vertical")


# ---- _intersects -----------------------------------------------------------

def test_intersects_catches_the_45_percent_clip_that_overlaps_any_missed():
    # The real job-2 p41 geometry: patch (199,0,474,139) vs bubble (128,90,156,59)
    # -> ~45% of the bubble covered, IoU 0.06, so `_overlaps_any` said "no overlap".
    assert _intersects((199, 0, 474, 139), (128, 90, 156, 59)) is True
    assert _intersects((128, 90, 156, 59), (199, 0, 474, 139)) is True


def test_intersects_is_false_for_adjacent_and_disjoint_boxes():
    assert _intersects((0, 0, 10, 10), (10, 0, 10, 10)) is False   # touching edge
    assert _intersects((0, 0, 10, 10), (0, 20, 10, 10)) is False   # below
    assert _intersects((0, 0, 10, 10), (5, 5, 10, 10)) is True     # corner clip


# ---- _drop_spurious_carryover ---------------------------------------------

def test_spurious_patch_over_own_text_is_dropped():
    spurious = (b"patch", (199, 0, 474, 139))
    innocent = (b"patch2", (0, 1400, 600, 200))
    kept = _drop_spurious_carryover([spurious, innocent], [_blk((128, 90, 156, 59))])
    assert kept == [innocent]


def test_patch_is_kept_when_the_page_owns_no_text_there():
    # The legitimate straddle: page 24 has zero blocks, its bubble is carried over
    # from page 23, so the patch MUST survive.
    carry = [(b"patch", (199, 0, 474, 139))]
    assert _drop_spurious_carryover(carry, []) == carry


def test_patch_is_kept_when_own_text_is_elsewhere():
    carry = [(b"patch", (0, 0, 300, 120))]
    assert _drop_spurious_carryover(carry, [_blk((500, 900, 100, 200))]) == carry


def test_no_carryover_is_not_an_error():
    assert _drop_spurious_carryover(None, [_blk((0, 0, 10, 10))]) == []


# ---- _free_text_region -----------------------------------------------------

def test_tall_narrow_column_gets_a_wide_strip():
    tall = (581, 795, 126, 323)          # the real job-4 p5 block
    r = _free_text_region(tall, 1125, 1600)
    assert r[2] > tall[2] * 2            # meaningfully wider than the column
    assert r[2] <= int(1125 * 0.6)       # capped at 60% of the page
    assert r[0] >= 0 and r[0] + r[2] <= 1125   # stays on-page


def test_wide_text_keeps_its_own_box():
    wide = (108, 129, 512, 121)          # a wide footnote / caption
    assert _free_text_region(wide, 1125, 1600) == wide


def test_near_square_text_keeps_its_own_box():
    sq = (100, 100, 120, 150)            # h/w = 1.25 < 1.5
    assert _free_text_region(sq, 1125, 1600) == sq


# ---- blank carryover patches ----------------------------------------------

def test_blank_carryover_patch_is_detected():
    # The real job-2 page-30 patch: 99.9% flat dark, zero lettering (std 10.8).
    flat = np.full((83, 196, 3), 45, dtype=np.uint8)
    assert _patch_is_blank(flat) is True
    assert _patch_is_blank(np.zeros((0, 0, 3), dtype=np.uint8)) is True
    assert _patch_is_blank(None) is True


def test_patch_with_lettering_is_not_blank():
    # A balloon patch: white balloon + black glyphs (the real page-24 patch, std 49).
    patch = np.full((60, 120, 3), 240, dtype=np.uint8)
    patch[20:40, 20:100] = 0
    assert _patch_is_blank(patch) is False


def test_extract_carryover_drops_a_blank_patch():
    page_h = 100
    result = np.full((160, 200, 3), 45, dtype=np.uint8)  # bare background below the cut
    targets = [(None, (10, 90, 100, 50))]                # region straddles page_h
    assert _extract_carryover(result, targets, page_h) == []


def test_extract_carryover_keeps_a_patch_that_has_lettering():
    page_h = 100
    result = np.full((160, 200, 3), 45, dtype=np.uint8)
    result[100:140, 10:110] = 240      # balloon/lettering drawn into the strip
    result[105:118, 20:70] = 0         # glyphs — the contrast that marks real text
    out = _extract_carryover(result, [(None, (10, 90, 100, 50))], page_h)
    assert len(out) == 1
    _patch, box = out[0]
    assert box == (10, 0, 100, 40)
