"""Repeated-detection collapse: the screentone duplicate failure (v0.27.12).

Job-4 page 118 rendered five overlapping copies of the same English line because the
OCR box detector fired six times over one patch of halftone — all six reading
そういえば/そういうことで — and every copy was translated and lettered into the same
spot. `_collapse_duplicate_blocks` fixes that without any pixel/texture heuristic
(measured: texture does NOT separate halftone from real thin-stroke glyphs).

Boxes below are the real ones from the job-4 DB, so the tests exercise the geometry
that actually failed.
"""
from app.pipeline.render import (
    _collapse_duplicate_blocks,
    _norm_utterance,
    _same_utterance,
)
from app.pipeline.types import TextBlock


def blk(box, text="", translation=""):
    return TextBlock(bbox=tuple(box), text=text, translation=translation)


# --- utterance identity -------------------------------------------------------

def test_punctuation_and_marks_are_ignored():
    assert _norm_utterance("そういえば、") == _norm_utterance("そういえば．．．")
    assert _norm_utterance("「あっ！」") == "あっ"
    assert _same_utterance("そういえば、", "そういえば．．．")


def test_different_lines_are_not_the_same_utterance():
    assert not _same_utterance("それでも、", "そういえば、")
    assert not _same_utterance("はい", "いいえ")
    assert not _same_utterance("", "はい")


def test_whole_region_and_its_subline_are_not_the_same_utterance():
    # The worker returns a region AND its individual lines; the length guard keeps
    # the granularity pair from being read as one "duplicate" utterance.
    long = "あってますけど！！なんでそれはわかるかなあ！"
    short = "あってますけど！！"
    assert not _same_utterance(long, short)


# --- the screentone cluster (job-4 page 118, the visible case) ----------------

P118 = [
    ([561, 544, 23, 238], "そういうことで、"),
    ([543, 547, 24, 235], "そういえば、"),
    ([504, 550, 21, 194], "そういえば．．．"),
    ([530, 552, 23, 225], "そういえば．．．"),
    ([525, 552, 18, 219], "そういえば、"),
    ([516, 553, 21, 208], "そういえば、"),
]


def test_page_118_phantom_run_disappears_entirely():
    """Six jittered boxes over pure screentone: 5 identical + 1 variant across the
    end of the run. Nothing here is real text, so nothing may be lettered."""
    blocks = [blk(b, t) for b, t in P118]
    assert _collapse_duplicate_blocks(blocks) == []


def test_a_lone_variant_inside_a_phantom_run_goes_with_it():
    blocks = [blk(b, t) for b, t in P118[1:]] + [blk([561, 544, 23, 238], "そういうことで、")]
    assert _collapse_duplicate_blocks(blocks) == []


# --- two-member duplicates ----------------------------------------------------

def test_a_cluster_keeps_its_most_complete_reading():
    # job-4 page 113: four jittered それでも、 boxes plus the full line they truncate
    blocks = [
        blk([478, 1103, 28, 226], "それでも、"),
        blk([472, 1104, 19, 225], "それでも、"),
        blk([491, 1106, 21, 213], "それでも、"),
        blk([504, 1115, 24, 187], "それでも、"),
        blk([992, 1368, 26, 230], "それでも、これからは、"),
        blk([1011, 1346, 25, 252], "これからは、"),
        blk([984, 1376, 25, 222], "それでも、"),
        blk([960, 1378, 36, 220], "それでも、"),
    ]
    kept = _collapse_duplicate_blocks(blocks)
    assert [b.text for b in kept] == ["それでも、これからは、"]


def test_a_truncated_duplicate_is_the_one_dropped():
    # job-4 page 60: the short reading sits inside the full one
    blocks = [
        blk([989, 52, 66, 314], "あってますけど！！"),
        blk([900, 61, 148, 292], "あってますけど！！なんでそれはわかるかなあ！"),
    ]
    kept = _collapse_duplicate_blocks(blocks)
    assert [b.text for b in kept] == ["あってますけど！！なんでそれはわかるかなあ！"]


# --- things that must NOT be touched -----------------------------------------

def test_same_line_in_two_places_is_two_balloons_not_a_duplicate():
    blocks = [
        blk([100, 100, 40, 120], "はい"),
        blk([800, 900, 40, 120], "はい"),
    ]
    assert len(_collapse_duplicate_blocks(blocks)) == 2


def test_boxes_that_only_clip_a_corner_are_left_alone():
    # job-4 page 58: two drawn `ふる` SFX overlapping across 12x1px
    blocks = [
        blk([355, 1249, 36, 52], "ふる"),
        blk([333, 1300, 34, 60], "ふる"),
    ]
    assert len(_collapse_duplicate_blocks(blocks)) == 2


def test_distinct_adjacent_columns_are_left_alone():
    blocks = [
        blk([400, 100, 20, 400], "それでも、"),
        blk([430, 100, 20, 400], "そういえば、"),
    ]
    assert len(_collapse_duplicate_blocks(blocks)) == 2


def test_a_real_line_next_to_the_run_is_kept():
    blocks = [
        blk([478, 1103, 28, 226], "それでも、"),
        blk([472, 1104, 19, 225], "それでも、"),
        blk([491, 1106, 21, 213], "それでも、"),
        blk([315, 1001, 125, 238], "最近調子いいですね！イルムガンドさん"),
    ]
    kept = _collapse_duplicate_blocks(blocks)
    assert [b.text for b in kept] == ["最近調子いいですね！イルムガンドさん"]


def test_single_block_and_empty_input_are_untouched():
    one = [blk([10, 10, 20, 20], "あ")]
    assert _collapse_duplicate_blocks(one) == one
    assert _collapse_duplicate_blocks([]) == []
