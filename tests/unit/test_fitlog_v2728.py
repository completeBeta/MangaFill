"""Tests for the v0.27.28 logging additions.

Covers the class-C detector (a region far too small for its text), the erase record,
and the meta record that makes a dump traceable to a build — added after a container
was found running a MIX of file versions, which silently invalidated a round of
"verified" results.
"""
import json
import logging
import os

import pytest

from app.pipeline import fitlog


@pytest.fixture()
def logging_on(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("")
    jsonl = tmp_path / "dump.jsonl"
    monkeypatch.setattr(fitlog, "SENTINEL", str(sentinel))
    monkeypatch.setattr(fitlog, "JSONL", str(jsonl))
    monkeypatch.setattr(fitlog, "_meta_written", False)
    fitlog.reset()
    yield jsonl
    fitlog.reset()


def _rows(jsonl):
    return [json.loads(l) for l in open(jsonl) if l.strip()]


def test_degenerate_region_is_recorded(logging_on):
    """The class-C signal must land in the dump and in the page summary."""
    fitlog.begin_page("00063.jpg")
    fitlog.record_degenerate((702, 457, 197, 8), "It hasn't gone up at all!",
                             "thin_region", {"w": 197, "h": 8, "chars": 25})
    rows = _rows(logging_on)
    deg = [r for r in rows if r["kind"] == "degenerate"]
    assert len(deg) == 1, rows
    assert deg[0]["reason"] == "thin_region"
    assert deg[0]["detail"]["h"] == 8
    assert deg[0]["bbox"] == [702, 457, 197, 8]


def test_meta_record_names_the_build(logging_on):
    """A dump without a version is untraceable — that trap cost a whole round of QA."""
    fitlog.record_region((0, 0, 10, 10), "container", (0, 0, 10, 10))
    meta = [r for r in _rows(logging_on) if r["kind"] == "meta"]
    assert meta, "no meta record written"
    assert meta[0]["version"], "meta record has no version"


def test_page_summary_counts_degenerate_and_unmeasurable(logging_on, caplog):
    import app.services.logging as applog  # noqa: F401
    fitlog.begin_page("00063.jpg")
    fitlog.record_block((0, 0, 20, 8), "long text here", (0, 0, 20, 8),
                        None, None, 8, 2, 8, None, False, 1, trace_ok=False)
    fitlog.record_degenerate((702, 457, 197, 8), "thin text", "thin_region")
    with caplog.at_level(logging.INFO):
        fitlog.end_page()
    msgs = [r.getMessage() for r in caplog.records]
    line = [m for m in msgs if "FITLOG page=00063.jpg" in m]
    assert line, msgs
    assert "degenerate=1" in line[0]
    assert "unmeasurable=1" in line[0]


def test_erase_record_captures_the_footprint(logging_on):
    """Erase + lettering together is what makes a blank area diagnosable."""
    fitlog.begin_page("00063.jpg")
    fitlog.record_erase((0, 0, 1125, 1600), 41234, 7, {"blocks": 9})
    er = [r for r in _rows(logging_on) if r["kind"] == "erase"]
    assert er and er[0]["pixels"] == 41234 and er[0]["boxes"] == 7


def test_degenerate_detector_fires_on_a_thin_region(tmp_path, monkeypatch):
    """_draw_box must flag a region that cannot possibly hold its text."""
    from PIL import Image, ImageDraw
    from app.pipeline import typeset
    sentinel = tmp_path / "s"
    sentinel.write_text("")
    jsonl = tmp_path / "d.jsonl"
    monkeypatch.setattr(fitlog, "SENTINEL", str(sentinel))
    monkeypatch.setattr(fitlog, "JSONL", str(jsonl))
    monkeypatch.setattr(fitlog, "_meta_written", False)
    fp = typeset.resolve_font_path(None)
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    fitlog.begin_page("00063.jpg")
    # a 197x8 region carrying a 25-character sentence
    typeset._draw_box(img, d, (0, 0, 197, 8), "It hasn't gone up at all!", fp, 35)
    deg = [r for r in _rows(jsonl) if r["kind"] == "degenerate"]
    assert deg, "thin region with long text was not flagged"
    assert deg[0]["reason"] == "thin_region"


def test_pages_do_not_share_accumulator_state(logging_on):
    """v0.27.28 shipped a shared-mutable template, so `sizes` and `branches` were the
    SAME objects on every page: counters came out cumulative and `sizes` grew without
    bound. The accumulator must be rebuilt fresh per page."""
    from app.pipeline import fitlog
    fitlog.reset()
    fitlog.begin_page("p1")
    fitlog.record_region((0, 0, 10, 10), "container", (0, 0, 10, 10))
    fitlog.record_block((0, 0, 10, 10), "hi", (0, 0, 10, 10), 8, 8, 8, 8, 9, 14, True, 2)
    assert fitlog._page["blocks"] == 1 and fitlog._page["sizes"] == [9]
    fitlog.end_page()
    fitlog.begin_page("p2")
    assert fitlog._page["blocks"] == 0, "counters carried over from the previous page"
    assert fitlog._page["sizes"] == [], "sizes list was shared across pages"
    assert fitlog._page["branches"] == {}, "branches dict was shared across pages"


def test_recovery_and_unhandled_thin_region_are_counted_separately(logging_on):
    """The Logs tab must distinguish a HANDLED recovery from an UNHANDLED defect.

    Both used to land in `degenerate`, so an operator reading the summary could not
    tell "recovered, fine" from "still broken".
    """
    fitlog.reset(); fitlog.begin_page("p")
    fitlog.record_degenerate((0, 0, 10, 10), "some long text", "thin_region", {"w": 10, "h": 2})
    fitlog.record_degenerate((0, 0, 10, 10), "some long text", "region_recovered_from_page",
                             {"old": [0, 0, 10, 2], "new": [0, 0, 10, 20]})
    assert fitlog._page["degenerate"] == 1, fitlog._page
    assert fitlog._page["recovered"] == 1, fitlog._page
    fitlog.reset()
