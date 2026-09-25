"""Art-preserving erase + the ko/zh block guards (v0.27.14).

Three defects from the 2026-09-16 QA sweep of jobs 2/3/4, each with the real page
that produced it:

* **Erasing a block's whole rectangle destroys artwork.** Job-2 page 38's drawn
  `튼다다` (502x735, over a fire-lit panel) came back as a grey block; page 84's
  `조~으~옹..` flattened a blue sky. The ink inside those boxes is only 9-19% of
  the area, so the erase mask is now built from the strokes.
* **A drawn SFX merged into the narration above it.** Page 56's three-line
  narration (60-70px lines) swallowed the 384x457 drawn `치이즈` below it, so the
  erase box covered both, the translation landed between them and the narration
  panel came back EMPTY with the English over the erased art.
* **A shop sign carrying English was re-lettered as nonsense.** Page 46's sign came
  back as `DPENON.ETER 40주GRAND OPEN 4125~12125EYET` and was translated +
  lettered over a washed-out shop front.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.inpaint import _fill_holes, stroke_boxes
from app.pipeline.render import (
    _ascii_share,
    _drop_english_signage,
    _drop_stub_art,
    _erase_rects,
    _stack_adjacent,
)
from app.pipeline.types import TextBlock


def _page(h: int = 240, w: int = 240, bg: int = 250) -> np.ndarray:
    return np.full((h, w), bg, dtype=np.uint8)


def _ink_cover(mask_shape: tuple, rects: list[tuple], box: tuple = (0, 0, 0, 0)) -> np.ndarray:
    cover = np.zeros(mask_shape, dtype=bool)
    for (rx, ry, rw, rh) in rects:
        x0, y0 = max(0, rx - box[0]), max(0, ry - box[1])
        x1, y1 = min(mask_shape[1], rx - box[0] + rw), min(mask_shape[0], ry - box[1] + rh)
        if x1 > x0 and y1 > y0:
            cover[y0:y1, x0:x1] = True
    return cover


# --- stroke_boxes -------------------------------------------------------------

def test_stroke_boxes_shrink_a_text_over_art_box():
    """A thin ink stroke on a light background: cells hug the stroke, not the box."""
    page = _page(400, 400)
    page[300:316, 40:360] = 20  # one thick stroke low inside the box
    box = (20, 80, 360, 300)
    rects = stroke_boxes(page, box)
    assert rects, "must never return nothing"
    area = sum(r[2] * r[3] for r in rects)
    assert area < 0.75 * (box[2] * box[3]), "the mask should cover far less than the box"
    ink = np.zeros((box[3], box[2]), dtype=bool)
    ink[300 - 80:316 - 80, 40 - 20:360 - 20] = True  # the stroke, box coordinates
    cover = _ink_cover(ink.shape, rects, box)
    assert (ink & ~cover).sum() == 0, "every stroke pixel must be inside the mask"


def test_stroke_boxes_falls_back_to_the_box_when_ink_fills_it():
    """Dense text has nothing to protect — keep the plain rectangle."""
    page = _page(120, 120)
    page[10:110, 10:110] = 20  # fully inked box
    assert stroke_boxes(page, (0, 0, 120, 120)) == [(0, 0, 120, 120)]


def test_stroke_boxes_keeps_small_boxes_intact():
    page = _page(60, 60)
    page[20:30, 20:40] = 0
    assert stroke_boxes(page, (10, 10, 40, 40)) == [(10, 10, 40, 40)]


def test_hole_filling_covers_a_drawn_glyph_body():
    """A drawn glyph is an outline: its face is background by colour but must be
    erased, or the glyph survives as a ghost (page 84's dark blob in the sky)."""
    ink = np.zeros((120, 120), dtype=bool)
    ink[20:100, 20:100] = True
    ink[30:90, 30:90] = False  # ring: a glyph outline with a large flat interior
    filled = _fill_holes(ink)
    assert filled[60, 60], "the enclosed glyph face must be filled"
    assert not filled[5, 5], "the open background must stay open"


def test_hole_filling_leaves_an_enclosed_poster_alone():
    """Page 98's lettuce poster is enclosed by its own border — art, not a glyph."""
    ink = np.zeros((400, 400), dtype=bool)
    ink[20:380, 20:380] = True
    ink[40:360, 40:360] = False  # a big panel interior
    filled = _fill_holes(ink)
    assert not filled[200, 200], "a poster interior is bigger than any glyph face"


# --- the stacked-line merge guard (page 56) -----------------------------------

def test_stacked_merge_rejects_a_drawn_sfx_under_a_narration_box():
    narration_line = (256, 452, 206, 71)
    drawn_sfx = (0, 651, 384, 457)  # `치이즈`, one OCR glyph, 6x the line height
    assert not _stack_adjacent(narration_line, drawn_sfx)
    assert not _stack_adjacent(drawn_sfx, narration_line)


def test_stacked_merge_still_joins_real_lines():
    a = (256, 452, 206, 71)
    b = (267, 519, 184, 61)
    c = (252, 577, 212, 67)
    assert _stack_adjacent(a, b)
    assert _stack_adjacent(b, c)


def test_stacked_merge_still_joins_a_short_last_line():
    """A short trailing line ("...", "?!") must not be split off."""
    line = (100, 300, 300, 60)
    short = (140, 366, 60, 55)
    assert _stack_adjacent(line, short)


# --- ko/zh block guards -------------------------------------------------------

def test_ascii_share_of_a_sign_is_high():
    assert _ascii_share("DPENON.ETER 40주GRAND OPEN 4125~12125EYET") > 0.6
    assert _ascii_share("단지 맛이 끔찍하게 없었을 뿐.") < 0.1


def test_english_signage_is_left_alone():
    sign = TextBlock(bbox=(137, 1030, 395, 259), text="DPENON.ETER 40주GRAND OPEN", confidence=0.9)
    dialogue = TextBlock(bbox=(89, 1543, 307, 213), text="숨막히는 도시 생활", confidence=0.99)
    kept = _drop_english_signage([sign, dialogue], bubbles=[])
    assert kept == [dialogue]


def test_english_text_inside_a_bubble_is_still_dialogue():
    """A character speaking an English line in a bubble is not signage."""
    b = TextBlock(bbox=(10, 10, 100, 100), text="OK BOSS", confidence=0.9)
    bubble = (0, 0, 200, 200)
    assert _drop_english_signage([b], bubbles=[bubble]) == [b]


def test_one_glyph_read_of_drawn_art_is_dropped():
    stub = TextBlock(bbox=(0, 651, 384, 457), text="철", confidence=0.98)
    assert _drop_stub_art([stub], bubbles=[]) == []


def test_two_glyph_drawn_sfx_is_kept_and_translated():
    """`조용` -> "Quiet." is a real reading of a drawn SFX (page 84)."""
    sfx = TextBlock(bbox=(0, 680, 690, 421), text="조용", confidence=0.95)
    assert _drop_stub_art([sfx], bubbles=[]) == [sfx]


def test_a_lone_glyph_in_a_bubble_is_kept():
    b = TextBlock(bbox=(0, 0, 200, 200), text="뭐", confidence=0.99)
    assert _drop_stub_art([b], bubbles=[(0, 0, 260, 260)]) == [b]


def test_small_single_glyph_blocks_are_kept():
    b = TextBlock(bbox=(10, 10, 60, 60), text="뭐", confidence=0.99)
    assert _drop_stub_art([b], bubbles=[]) == [b]


# --- _erase_rects -------------------------------------------------------------

def test_erase_rects_keeps_small_boxes_as_one_rect():
    page = _page(200, 200)
    assert _erase_rects(page, (10, 10, 60, 60)) == [(10, 10, 60, 60)]


def test_erase_rects_uses_strokes_for_a_big_box_on_art():
    page = _page(400, 400)
    page[300:316, 40:360] = 15  # a drawn glyph stroke low in the box
    rects = _erase_rects(page, (20, 80, 360, 300))
    assert rects
    assert sum(r[2] * r[3] for r in rects) < 0.75 * (360 * 300)


def test_erase_rects_without_a_page_falls_back():
    assert _erase_rects(None, (0, 0, 500, 500)) == [(0, 0, 500, 500)]


def test_erase_rects_never_raises_on_a_degenerate_box():
    page = _page(20, 20)
    assert _erase_rects(page, (0, 0, 0, 0)) == [(0, 0, 0, 0)]
