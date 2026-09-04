"""Unit tests for detector helpers (pure functions — no model load)."""
from app.pipeline.detector import find_parent_bubble, _contains


def test_contains():
    assert _contains((0, 0, 10, 10), 5, 5) is True
    assert _contains((0, 0, 10, 10), 11, 5) is False
    assert _contains((0, 0, 10, 10), 0, 0) is True  # boundary inclusive


def test_find_parent_bubble_smallest():
    bubbles = [(0, 0, 100, 100), (10, 10, 40, 40)]
    # center of (20,20,10,10) = (25,25) is inside both; smallest wins
    assert find_parent_bubble(bubbles, (20, 20, 10, 10)) == (10, 10, 40, 40)


def test_find_parent_bubble_none():
    assert find_parent_bubble([(0, 0, 10, 10)], (50, 50, 10, 10)) is None
