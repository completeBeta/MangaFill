"""Lookahead must not be OCR'd together with the page (2026-09-15).

PaddleOCR's recognition is size-sensitive, so handing it the wrong image size
collapses recognition into garbage syllables:

    page 77, native   : '현성아!' (conf 0.999) + 6 more clean lines
    page 77, stitched : '야워을' (conf 0.609), '욱ㄴ', '급야골', '극의못', ...

Those garbage boxes only partly covered the real text, so the Korean survived
erasure while English was lettered over it. `_ocr_ko_zh_native` OCRs the page and
the boundary band as separate images at a consistent resolution and maps the band
back into page coordinates.
"""
import numpy as np
import pytest
from PIL import Image

import app.pipeline.render as render
from app.pipeline.types import TextBlock


def _seq_ocr(page_boxes, band_boxes, calls):
    """Fake recognizer: call 1 = the page pass, call 2 = the band pass."""
    state = {"n": 0}

    def fake(image, url, lang):
        calls.append((image.width, image.height, lang))
        state["n"] += 1
        return list(page_boxes if state["n"] == 1 else band_boxes)
    return fake


# ---------------------------------------------------------------- band geometry

def test_band_is_rescaled_to_the_page_long_side(monkeypatch):
    """The raw 500+500 join is misrecognised; rescaled to page height it is not.

    Measured on job-2 page 68: '이 위아래로' came back as '래러이ㅎ' (conf 0.999 ->
    0.670) from the raw 690x1000 join on BOTH the GPU worker and the CPU fallback,
    while the identical pixels read cleanly at the page's own resolution.
    """
    calls = []
    monkeypatch.setattr(render, "remote_ocr_multilingual",
                        _seq_ocr([], [], calls))
    page = Image.new("RGB", (200, 400), "white")
    lookahead = np.zeros((500, 200, 3), dtype=np.uint8)
    render._ocr_ko_zh_native(page, np.asarray(page), lookahead, "http://w", "ko",
                            band=150)
    assert len(calls) == 2
    assert calls[0] == (200, 400, "ko")                 # the page, native
    w, h, _ = calls[1]                                   # the rescaled band
    assert h == page.height, "band must be scaled to the page's long side"
    # join is 200x300 -> sc = 400/300 -> width 200 * 400/300 = 267
    assert abs(w - 267) <= 1, f"band width {w} not rescaled with the join"


def test_band_boxes_are_returned_as_integers(monkeypatch):
    """The rescale makes box coords fractional; downstream array slicing needs ints.

    v0.27.4 shipped the fractional coords and 10 of job 2's 129 pages died with
    "'float' object cannot be interpreted as an integer".
    """
    calls = []
    monkeypatch.setattr(render, "remote_ocr_multilingual",
                        _seq_ocr([], [((10, 140, 50, 20), "BAND", 0.9, 0.0)], calls))
    page = Image.new("RGB", (200, 400), "white")
    lookahead = np.zeros((500, 200, 3), dtype=np.uint8)
    out = render._ocr_ko_zh_native(page, np.asarray(page), lookahead, "http://w",
                                   "ko", band=150)
    assert len(out) == 1
    box, text, _c, _a = out[0]
    assert text == "BAND"
    assert all(isinstance(v, int) for v in box), f"non-integer box: {box}"


def test_custom_recognizer_is_used_for_both_passes():
    """The local PP-OCR fallback gets the same native+band treatment."""
    seen = []

    def ocr_fn(img, lang):
        seen.append((img.width, img.height))
        return [((1, 1, 10, 10), "T", 0.9, 0.0)]

    page = Image.new("RGB", (200, 400), "white")
    lookahead = np.zeros((500, 200, 3), dtype=np.uint8)
    render._ocr_ko_zh_native(page, np.asarray(page), lookahead, None, "ko",
                             band=150, ocr_fn=ocr_fn)
    assert seen[0] == (200, 400)
    assert seen[1][1] == 400            # band rescaled to the page's long side


def test_no_lookahead_is_a_single_native_call(monkeypatch):
    calls = []
    monkeypatch.setattr(render, "remote_ocr_multilingual",
                        _seq_ocr([((10, 10, 50, 20), "PAGE", 0.99, 0.0)], [], calls))
    page = Image.new("RGB", (200, 400), "white")
    out = render._ocr_ko_zh_native(page, np.asarray(page), None, "http://w", "ko")
    assert len(calls) == 1
    assert [t for _b, t, _c, _a in out] == ["PAGE"]


def test_band_is_capped_by_the_page_height(monkeypatch):
    calls = []
    monkeypatch.setattr(render, "remote_ocr_multilingual", _seq_ocr([], [], calls))
    page = Image.new("RGB", (200, 400), "white")
    short = np.zeros((50, 200, 3), dtype=np.uint8)
    render._ocr_ko_zh_native(page, np.asarray(page), short, "http://w", "ko",
                             band=500)
    # band = min(500, page_h=400, strip=50) = 50 -> join is 100 tall, rescaled to 400
    assert calls[1][1] == 400


def test_wrong_width_lookahead_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(render, "remote_ocr_multilingual",
                        _seq_ocr([((1, 1, 10, 10), "PAGE", 0.9, 0.0)], [], calls))
    page = Image.new("RGB", (200, 400), "white")
    odd = np.zeros((500, 199, 3), dtype=np.uint8)
    out = render._ocr_ko_zh_native(page, np.asarray(page), odd, "http://w", "ko")
    assert len(calls) == 1
    assert [t for _b, t, _c, _a in out] == ["PAGE"]


# ---------------------------------------------------------------- band handover

def test_boundary_line_is_not_read_twice():
    """Both passes see the boundary line; the LLM must get it once.

    Job-2 page 77, in the app: page pass AND band pass both returned '살아!',
    `_merge_horizontal_words` joined them into '살아! 살아!', and the page came
    out reading "Live here! Live here!".
    """
    page = [((10, 100, 50, 20), "살아!", 0.99, 0.0)]
    band = [((10, 101, 50, 20), "살아!", 0.99, 0.0)]   # same line, band's copy
    out = render._band_handover(page, band, 400)
    assert [t for _b, t, _c, _a in out] == ["살아!"]


def test_band_read_of_a_crossing_bubble_replaces_the_partial_read():
    """A bubble cut by the page edge: keep the band's whole reading, not the top half."""
    page = [((100, 200, 200, 200), "PARTIAL", 0.9, 0.0)]       # top half only
    band = [((100, 200, 200, 400), "WHOLE BUBBLE", 0.99, 0.0)]  # crosses page_h
    out = render._band_handover(page, band, 400)
    assert [t for _b, t, _c, _a in out] == ["WHOLE BUBBLE"]


def test_next_page_text_from_the_band_is_kept():
    """Text below the cut still reaches the pipeline (erase + carryover)."""
    page = [((10, 100, 50, 20), "PAGE", 0.9, 0.0)]
    band = [((10, 430, 50, 20), "NEXT", 0.9, 0.0)]
    out = render._band_handover(page, band, 400)
    assert sorted(t for _b, t, _c, _a in out) == ["NEXT", "PAGE"]


def test_handover_without_a_band_is_a_noop():
    page = [((10, 100, 50, 20), "PAGE", 0.9, 0.0)]
    assert render._band_handover(page, [], 400) == page


# ------------------------------------------------------------- per-bubble merge

def test_blocks_in_one_bubble_are_merged():
    """job-2 page 34: 'CAN YOU HEAR ME?' was lettered through 'L-SENBAE! SENBAE!'."""
    bubbles = [(0, 400, 320, 400)]
    a = TextBlock(bbox=(1, 455, 311, 268), text="L선배!선배!", confidence=0.99,
                  orientation="horizontal")
    b = TextBlock(bbox=(139, 659, 141, 117), text="내말들려요?", confidence=0.99,
                  orientation="horizontal")
    out = render._merge_blocks_per_bubble([a, b], bubbles)
    assert len(out) == 1
    assert out[0].text == "L선배!선배! 내말들려요?"
    assert out[0].bbox == (1, 455, 311, 321)  # union of both line boxes


def test_blocks_in_different_bubbles_stay_separate():
    bubbles = [(0, 0, 100, 100), (0, 300, 100, 100)]
    a = TextBlock(bbox=(10, 10, 40, 20), text="가", confidence=0.9)
    b = TextBlock(bbox=(10, 310, 40, 20), text="나", confidence=0.9)
    out = render._merge_blocks_per_bubble([a, b], bubbles)
    assert len(out) == 2
    assert [x.text for x in out] == ["가", "나"]


def test_free_floating_blocks_are_never_merged():
    """Text with no enclosing bubble (stat columns, captions) keeps its layout."""
    bubbles = [(0, 0, 100, 100)]
    a = TextBlock(bbox=(500, 500, 40, 20), text="caption one", confidence=0.9)
    b = TextBlock(bbox=(500, 540, 40, 20), text="caption two", confidence=0.9)
    out = render._merge_blocks_per_bubble([a, b], bubbles)
    assert len(out) == 2


def test_no_bubbles_is_a_noop():
    a = TextBlock(bbox=(0, 0, 10, 10), text="가")
    assert render._merge_blocks_per_bubble([a], []) == [a]
    assert render._merge_blocks_per_bubble([a], None) == [a]


# ---------------------------------------------------------------- merge snowball

def test_multiline_bubble_still_merges():
    """A four-line bubble must still become one block (the normal case)."""
    boxes = [
        ((416, 33, 100, 62), "아니,", 0.99, 0.0),
        ((369, 91, 196, 53), "그렇다기엔", 0.99, 0.0),
        ((357, 141, 219, 60), "너무생긴게", 0.99, 0.0),
        ((359, 194, 216, 59), "멀쩡한데?", 0.99, 0.0),
    ]
    out = render._merge_stacked_lines(boxes)
    assert len(out) == 1
    assert out[0][1] == "아니,그렇다기엔너무생긴게멀쩡한데?"


def test_distant_box_is_not_swept_into_a_bubble():
    """job-2 page 115: a growing union let three art misreads chain onto the bubble.

    After the four lines merged, the union was 220 px tall, so the union-derived
    gap test (0.6 * min(498, 220) = 132) admitted an art misread sitting 73 px
    below it — and then that box's own 498 px height admitted the next, ending in
    ONE 575x1417 block whose erase wiped the page's artwork. Measured gaps here
    are the real ones from that page; only the union rule merges them.
    """
    bubble = [
        ((416, 33, 100, 62), "아니,", 0.99, 0.0),
        ((369, 91, 196, 53), "그렇다기엔", 0.99, 0.0),
        ((357, 141, 219, 60), "너무생긴게", 0.99, 0.0),
        ((359, 194, 216, 59), "멀쩡한데?", 0.99, 0.0),
    ]
    noise = [((21, 326, 462, 498), "N", 0.9, 0.0)]
    out = render._merge_stacked_lines(bubble + noise)
    assert len(out) == 2, f"art misread was absorbed: {out}"
    texts = sorted(t for _b, t, _c, _a in out)
    assert texts == ["N", "아니,그렇다기엔너무생긴게멀쩡한데?"]
    # the bubble block must not have grown into the artwork
    blk = [b for b, t, _c, _a in out if t.startswith("아니")][0]
    assert blk[3] < 260, f"bubble block sprawled to height {blk[3]}"


def test_stack_adjacent_rejects_other_columns():
    a = ((10, 100, 50, 20), "", 0.0, 0.0)
    assert render._stack_adjacent((10, 130, 50, 20), a[0])       # next line
    assert not render._stack_adjacent((300, 130, 50, 20), a[0])  # other column
    assert not render._stack_adjacent((10, 400, 50, 20), a[0])   # far below
