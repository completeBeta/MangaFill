"""Class C: recover a lettering region that is far too small for its text.

Discovered on job-3 page 163: box [702,457,197,39] for まったく上がらす！, region
squeezed to 197x8, English lettered at 8px over a large blank area.

The rule under test: when the region is implausibly thin for its text, letter into at
least the footprint we erased for that block — but never wildly, and never such that
another block's box would be swallowed.
"""
import pytest

from app.pipeline import render


BOX = (702, 457, 197, 39)


def _patch_erase(monkeypatch, rects):
    monkeypatch.setattr(render, "_erase_rects", lambda gray, box: list(rects))


def test_recovers_from_the_erase_footprint_when_region_is_thin(monkeypatch):
    """The real p163 case: thin region, bigger erased footprint -> use the footprint."""
    _patch_erase(monkeypatch, [(700, 445, 300, 80)])
    got = render._recover_thin_region(None, BOX, (702, 457, 197, 8),
                                      "It hasn't gone up at all!", 1125, 1600, [BOX])
    assert got == (700, 445, 300, 80), got
    assert got[3] > 8, "the recovered height must exceed the squeezed region"


def test_leaves_a_usable_region_alone(monkeypatch):
    """A region that is already sane must not be touched — no regressions elsewhere."""
    _patch_erase(monkeypatch, [(0, 0, 900, 900)])
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 39),
                                       "It hasn't gone up at all!", 1125, 1600, [BOX]) \
        == (702, 457, 197, 39)


def test_leaves_short_text_alone(monkeypatch):
    """A thin box holding a couple of characters is a label, not a mis-detected line."""
    _patch_erase(monkeypatch, [(0, 0, 900, 900)])
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 8), "Ro",
                                       1125, 1600, [BOX]) == (702, 457, 197, 8)


def test_rejects_a_page_sized_erase_footprint(monkeypatch):
    """An erase that ran away must not become the lettering region."""
    _patch_erase(monkeypatch, [(0, 0, 1125, 1600)])
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 8),
                                       "It hasn't gone up at all!", 1125, 1600, [BOX]) \
        == (702, 457, 197, 8)


def test_rejects_growth_beyond_the_area_cap(monkeypatch):
    """8x the region area is the ceiling — beyond that we do not trust the footprint."""
    _patch_erase(monkeypatch, [(600, 400, 500, 500)])  # 250k vs 1.6k -> way over 8x
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 8),
                                       "It hasn't gone up at all!", 1125, 1600, [BOX]) \
        == (702, 457, 197, 8)


def test_refuses_to_swallow_a_neighbouring_block(monkeypatch):
    """Recovery must not steal a neighbouring block's box."""
    other = (760, 460, 120, 40)
    _patch_erase(monkeypatch, [(700, 445, 300, 80)])
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 8),
                                       "It hasn't gone up at all!", 1125, 1600,
                                       [BOX, other]) == (702, 457, 197, 8)


def test_never_raises(monkeypatch):
    """Instrumentation-adjacent code must not be able to break a render."""
    def boom(gray, box):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(render, "_erase_rects", boom)
    assert render._recover_thin_region(None, BOX, (702, 457, 197, 8),
                                       "It hasn't gone up at all!", 1125, 1600, [BOX]) \
        == (702, 457, 197, 8)
