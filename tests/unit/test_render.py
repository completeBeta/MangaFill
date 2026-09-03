"""Unit tests for render helpers (pure functions — no model load)."""
import numpy as np
from PIL import Image

from app.pipeline.render import (
    _box_containment,
    _dedup_boxes,
    _iou,
    _is_blank,
    _is_color,
    _orientation,
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
