"""Caption regions may only use space the page leaves free (v0.27.20).

`_caption_region` widens a tall-narrow source column so horizontal English has
room. Without the page it is a blind rectangle: on job-3 page 19 a 112px column's
strip was widened 153px to the RIGHT over the neighbouring column and lifted 1.5x
in height out of its panel, so two English letterings printed through each other
across the panel rule. With `gray` + `obstacles` the strip grows into free space
only: no ink (artwork / glyph strokes / panel rules) and no sibling block's box or
already-claimed region.

These are the guards that keep that promise. Synthetic pages only; the real-page
A/B lives in the render sweep.
"""
from __future__ import annotations

import numpy as np

from app.pipeline.render import (
    _CAPTION_OBSTACLE_GAP,
    _caption_blocked,
    _caption_region,
    _free_lines,
    _free_text_region,
    _run_room,
    _sibling_boxes,
)
from app.pipeline.types import TextBlock

PAGE_W, PAGE_H = 1125, 1600


def page() -> np.ndarray:
    return np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)


def black(a: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    a[y:y + h, x:x + w] = 20
    return a


def screentone(a: np.ndarray, x: int, y: int, w: int, h: int, step: int = 20) -> np.ndarray:
    """1px dots every `step` px: real screentone density (~5%), no stroke."""
    a[y:y + h:step, x:x + w:step] = 120
    return a


def block(box, orientation="vertical", text="あ"):
    return TextBlock(bbox=tuple(box), text=text, confidence=1.0, orientation=orientation)


def caps(box):
    """The region the strip may occupy at most (the pre-v0.27.20 caps)."""
    x, y, w, h = box
    nw = max(w, min(int(h * 1.5), int(PAGE_W * 0.6), int(w * 2.5) + 24)) if h > w * 1.5 else w
    nh = max(int(h * 1.5), int(PAGE_H * 0.06))
    return nw, nh


# ---- helpers ---------------------------------------------------------------

def test_caption_blocked_marks_ink_and_obstacle_boxes():
    g = page()
    black(g, 700, 600, 40, 40)
    b = _caption_blocked(g, [(100, 100, 50, 50)])
    assert b[610, 710]
    assert b[120, 120]
    assert not b[300, 300]


def test_free_lines_reports_stroke_and_darkness():
    g = page()
    black(g, 0, 300, PAGE_W, 3)          # a 3px panel rule
    screentone(g, 0, 500, PAGE_W, 100, step=20)  # ~5% dark, isolated dots
    blocked = _caption_blocked(g, [])
    cols = _free_lines(blocked, 299, 304, 0)   # columns crossed by the rule
    assert not cols.any()
    cols = _free_lines(blocked, 500, 600, 0)   # columns over light screentone
    assert cols.all()


def test_run_room_is_symmetric_and_capped():
    assert _run_room(np.ones(10, dtype=bool), 4, 6, 99) == 4      # 4 lines each side
    walled = np.ones(10, dtype=bool)
    walled[3] = False                                             # wall on the left
    assert _run_room(walled, 4, 5, 99) == 0
    assert _run_room(np.ones(100, dtype=bool), 40, 60, 12) == 12  # cap wins


# ---- region geometry -------------------------------------------------------

def test_legacy_path_unchanged_without_the_page():
    """No page -> the historical formula, and obstacles are ignored."""
    box = (754, 376, 72, 461)
    legacy = _caption_region(box, 1600, 2262)
    assert legacy == (754, 261, 204, 691)
    assert _caption_region(box, 1600, 2262, gray=None,
                           obstacles=[(600, 300, 300, 300)]) == legacy


def test_grows_to_the_caps_on_clear_page_and_stays_centred_on_source():
    box = (500, 700, 100, 200)
    nw, nh = caps(box)
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=page(), obstacles=[])
    assert (w, h) == (nw, nh)                       # free space -> full strip
    assert x + w // 2 == box[0] + box[2] // 2       # centred on the source column
    assert y + h // 2 == box[1] + box[3] // 2


def test_never_smaller_than_the_source_box():
    """Ink tight on all four sides: no growth, but the source box is kept whole."""
    box = (500, 700, 100, 200)
    g = page()
    black(g, 0, 696, PAGE_W, 4)            # panel rule above
    black(g, 0, 900, PAGE_W, 4)            # panel rule below
    black(g, 496, 700, 4, 200)             # rule left
    black(g, 600, 700, 4, 200)             # rule right
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=g, obstacles=[])
    assert (x, y, w, h) == box


def test_stays_on_page_at_the_edges():
    edge = (2, 2, 60, 200)                 # hugging the top-left corner
    x, y, w, h = _caption_region(edge, PAGE_W, PAGE_H, gray=page(), obstacles=[])
    assert x >= 0 and y >= 0 and x + w <= PAGE_W and y + h <= PAGE_H
    assert x <= edge[0] and y <= edge[1]


def test_box_outside_the_page_is_clamped():
    """Job-3 page 40 had a stored box wider than its page (x+w=797 on a 690px
    page) — the run must clamp, not walk off the end of the mask."""
    g = np.full((1600, 690), 255, dtype=np.uint8)
    box = (640, 300, 157, 400)             # x+w = 797 > 690
    x, y, w, h = _caption_region(box, 690, 1600, gray=g, obstacles=[])
    assert x >= 0 and x + w <= 690
    assert y >= 0 and y + h <= 1600
    wide = (10, 20, 900, 1500)             # past both edges
    x, y, w, h = _caption_region(wide, PAGE_W, PAGE_H, gray=page(), obstacles=[])
    assert x >= 0 and y >= 0 and x + w <= PAGE_W and y + h <= PAGE_H


def test_panel_rule_stops_vertical_growth():
    g = page()
    black(g, 0, 660, PAGE_W, 4)
    box = (500, 700, 100, 200)
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=g, obstacles=[])
    assert y == 664                        # strip stops below the rule, never crosses


def test_art_mass_stops_horizontal_growth():
    g = page()
    black(g, 700, 600, 200, 400)           # artwork to the right
    box = (500, 700, 100, 200)
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=g, obstacles=[])
    assert x + w <= 700


def test_sibling_block_stops_the_strip():
    g = page()
    box = (500, 700, 100, 200)
    sib = (640, 700, 100, 200)
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=g, obstacles=[sib])
    assert x + w <= sib[0] - _CAPTION_OBSTACLE_GAP
    assert x <= box[0] and x + w >= box[0] + box[2]


def test_light_screentone_stays_usable_lettering_space():
    g = page()
    screentone(g, 300, 600, 500, 400, step=20)
    box = (500, 700, 100, 200)
    x, y, w, h = _caption_region(box, PAGE_W, PAGE_H, gray=g, obstacles=[])
    assert w > box[2] and h > box[3]


# ---- whole-page invariants -------------------------------------------------

def test_two_columns_side_by_side_never_overlap():
    """The job-3 page-19 shape: two columns, each strip claiming the same gap."""
    g = page()
    box_a, box_b = (400, 700, 100, 200), (620, 700, 100, 200)
    blocks = [block(box_a), block(box_b)]
    claimed: list = []
    regions = []
    for b in blocks:
        r = _free_text_region(b.bbox, PAGE_W, PAGE_H, gray=g,
                              obstacles=_sibling_boxes(blocks, b) + claimed)
        regions.append(r)
        claimed.append(tuple(int(v) for v in r))
    (ax, ay, aw, ah), (bx, by, bw, bh) = regions
    assert ax + aw <= bx                      # disjoint, in reading order
    assert aw > box_a[2] and bw > box_b[2]    # both still got extra width


def test_region_never_contains_another_blocks_box():
    g = page()
    box = (500, 700, 100, 200)
    sibs = [(600, 660, 80, 280), (380, 640, 90, 260)]
    blocks = [block(box), block(sibs[0]), block(sibs[1])]
    x, y, w, h = _free_text_region(box, PAGE_W, PAGE_H, gray=g,
                                   obstacles=_sibling_boxes(blocks, blocks[0]))
    for sx, sy, sw, sh in sibs:
        assert x + w <= sx or sx + sw <= x or y + h <= sy or sy + sh <= y


def test_horizontal_free_text_keeps_its_own_box():
    g = page()
    wide = (200, 300, 300, 60)
    assert _free_text_region(wide, PAGE_W, PAGE_H, gray=g, obstacles=[]) == wide


def test_sibling_boxes_skips_nested_and_furigana():
    outer = (500, 700, 100, 200)
    inner = (520, 720, 40, 40)          # nested in outer -> same text
    furi = (610, 700, 12, 180)
    other = (700, 700, 100, 200)
    blocks = [block(outer), block(inner), block(furi, orientation="furigana"),
              block(other)]
    sibs = _sibling_boxes(blocks, blocks[0])
    assert inner not in sibs and furi not in sibs
    assert other in sibs
