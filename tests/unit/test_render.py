"""Unit tests for render helpers (pure functions — no model load)."""
import numpy as np
from PIL import Image

from app.pipeline.render import (
    _box_containment,
    _dedup_blocks,
    _dedup_boxes,
    _drop_non_japanese,
    _drop_titles,
    _iou,
    _is_blank,
    _is_color,
    _orientation,
    _split_bullet_lines,
)


def test_orientation_classifies_by_shape():
    assert _orientation(100, 30) == "horizontal"   # wide stat line
    assert _orientation(50, 60) == "horizontal"    # near-square
    assert _orientation(40, 200) == "vertical"     # tall name column
    assert _orientation(10, 40) == "furigana"      # narrow ruby column


def test_box_containment_nested():
    # 20x20 box fully inside a 100x100 box: containment is 1.0.
    assert _box_containment((0, 0, 100, 100), (10, 10, 20, 20)) == 1.0
    assert _box_containment((0, 0, 10, 10), (50, 50, 10, 10)) == 0.0


def test_dedup_drops_nested_and_overlapping():
    # A whole column plus its sub-fragments collapses to the largest box.
    boxes = [
        (10, 10, 20, 200),   # full column
        (10, 30, 20, 120),   # nested fragment
        (12, 12, 16, 80),    # nested fragment
        (10, 10, 20, 50),    # nested fragment
    ]
    kept = _dedup_boxes(boxes, seen=[])
    assert kept == [(10, 10, 20, 200)]


def test_dedup_keeps_distinct_boxes():
    boxes = [(0, 0, 50, 50), (100, 0, 50, 50), (0, 100, 50, 50)]
    kept = _dedup_boxes(boxes, seen=[])
    assert sorted(kept) == sorted(boxes)


def test_dedup_keep_smallest_drops_container_keeps_lines():
    # Horizontal stat text: the detector returns a whole stat box (container) plus
    # its individual lines. keep="smallest" keeps the lines, drops the container.
    boxes = [
        (0, 0, 400, 300),    # whole stat box
        (10, 10, 200, 40),   # line 1
        (10, 60, 200, 40),   # line 2
        (10, 110, 150, 40),  # line 3
    ]
    kept = _dedup_boxes(boxes, seen=[], keep="smallest")
    assert (0, 0, 400, 300) not in kept
    assert (10, 10, 200, 40) in kept
    assert (10, 60, 200, 40) in kept
    assert (10, 110, 150, 40) in kept


def test_device_resolve():
    from app.pipeline import device as d

    assert d.resolve("cpu") == "cpu"
    # auto/cuda resolve to "cuda" only when a CUDA torch is present; otherwise
    # they degrade to "cpu" — never an invalid value.
    assert d.resolve("auto") in ("cpu", "cuda")
    assert d.resolve("cuda") in ("cpu", "cuda")
    assert d.resolve("") in ("cpu", "cuda")


def test_is_blank_and_color():
    white = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert _is_blank(white) is True

    art = np.zeros((10, 10, 3), dtype=np.uint8)  # black page
    assert _is_blank(art) is False

    gray_img = Image.fromarray(np.full((10, 10, 3), 128, dtype=np.uint8))
    assert _is_color(gray_img) is False

    red = np.zeros((10, 10, 3), dtype=np.uint8)
    red[:, :, 0] = 255  # pure red — full chroma
    assert _is_color(Image.fromarray(red)) is True


def test_drop_non_japanese_filters_english():
    from app.pipeline.types import TextBlock

    jp = TextBlock(bbox=(0, 0, 10, 30), text="こんにちは", orientation="vertical")
    mixed = TextBlock(bbox=(0, 0, 10, 30), text="ジョン John", orientation="vertical")
    en = TextBlock(bbox=(0, 0, 40, 12), text="CONTENTS", orientation="horizontal")
    empty = TextBlock(bbox=(0, 0, 10, 10), text="", orientation="horizontal")

    kept = _drop_non_japanese([jp, mixed, en, empty])
    assert jp in kept       # Japanese -> translate
    assert mixed in kept    # has kana -> translate
    assert en not in kept   # already-English -> leave untouched
    assert empty not in kept


def test_dedup_blocks_drops_nested_duplicates():
    from app.pipeline.types import TextBlock

    # The same region detected whole + a nested sub-region -> keep the largest.
    whole = TextBlock(bbox=(100, 100, 300, 200), text="A B C D", orientation="vertical")
    sub = TextBlock(bbox=(110, 110, 280, 180), text="A B C", orientation="vertical")
    distinct = TextBlock(bbox=(500, 100, 100, 50), text="Z", orientation="horizontal")

    kept = _dedup_blocks([whole, sub, distinct])
    assert whole in kept        # largest kept
    assert sub not in kept      # nested duplicate dropped
    assert distinct in kept     # non-overlapping kept


def test_drop_titles_skips_large_text():
    from app.pipeline.types import TextBlock

    title = TextBlock(bbox=(0, 0, 800, 360), text="月が導く異世界道中", orientation="vertical")
    header = TextBlock(bbox=(0, 500, 500, 300), text="学園生徒の能力チェック", orientation="vertical")
    bio = TextBlock(bbox=(0, 900, 300, 170), text="あいうえお", orientation="vertical")
    tall_bio = TextBlock(bbox=(0, 1100, 300, 400), text="長い説明文が入る", orientation="vertical")
    kept = _drop_titles([title, header, bio, tall_bio], page_h=1600)
    assert title not in kept     # wide + tall -> title skipped
    assert header not in kept    # wide + tall -> header skipped
    assert bio in kept           # short -> kept
    assert tall_bio in kept      # tall but narrow -> bio kept


def test_split_bullet_lines_splits_stat_columns():
    from app.pipeline.types import TextBlock

    b = TextBlock(bbox=(100, 100, 200, 120), text="●筋力Ｂ＋●持久力Ｂ●防御技術Ｂ", orientation="vertical")
    out = _split_bullet_lines([b])
    assert [o.text for o in out] == ["筋力Ｂ＋", "持久力Ｂ", "防御技術Ｂ"]
    assert all(o.orientation == "horizontal" for o in out)


def test_split_bullet_lines_keeps_non_bullet():
    from app.pipeline.types import TextBlock

    b = TextBlock(bbox=(0, 0, 100, 50), text="こんにちは", orientation="vertical")
    assert _split_bullet_lines([b]) == [b]
