"""Phase 0 tests: the fit instrumentation must record decisions and NEVER break a render.

The ordering case (`record_region` called during region resolution, before
`typeset_page` has called `begin_page`) is the bug this file exists for: it surfaced
as a failed page ('branches') and a unit-test KeyError.
"""
from __future__ import annotations

import json

import pytest

from app.pipeline import fitlog


@pytest.fixture()
def logging_on(tmp_path, monkeypatch):
    """Opt the instrument in, pointed at throwaway files."""
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("")
    monkeypatch.setattr(fitlog, "SENTINEL", str(sentinel))
    monkeypatch.setattr(fitlog, "JSONL", str(tmp_path / "dump.jsonl"))
    monkeypatch.delenv("MF_FITLOG", raising=False)
    fitlog.begin_page("test")
    yield tmp_path / "dump.jsonl"
    fitlog.begin_page("reset")


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_disabled_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(fitlog, "SENTINEL", str(tmp_path / "nope"))
    monkeypatch.setattr(fitlog, "JSONL", str(tmp_path / "dump.jsonl"))
    monkeypatch.delenv("MF_FITLOG", raising=False)
    assert fitlog.enabled() is False
    fitlog.record_region((0, 0, 10, 10), "container", (0, 0, 10, 10))
    fitlog.record_block((0, 0, 10, 10), "hi", (0, 0, 10, 10), 8, 8, 8, 8, 12, 12, True, 1)
    fitlog.end_page()
    assert not (tmp_path / "dump.jsonl").exists()


def test_record_region_is_safe_before_begin_page(logging_on):
    """THE REGRESSION: render resolves regions before typeset_page calls begin_page()."""
    fitlog._page.clear()  # simulate a fresh process / no begin_page yet
    fitlog.record_region((1, 2, 3, 4), "detector-bubble", (1, 2, 3, 4),
                         {"container_note": "none"})
    rows = [r for r in _lines(logging_on) if r["kind"] == "region"]
    assert len(rows) == 1
    assert rows[0]["branch"] == "detector-bubble"
    assert rows[0]["bbox"] == [1, 2, 3, 4]


def test_record_block_is_safe_before_begin_page(logging_on):
    fitlog._page.clear()
    fitlog.record_block((0, 0, 10, 10), "hi", (0, 0, 10, 10), None, None, 8, 8, 12, 12,
                        False, 1)
    assert [r for r in _lines(logging_on) if r["kind"] == "block"]


def test_records_the_under_size_metric(logging_on):
    """chosen < largest_fitting is the whole-job audit signal, and it also keeps the
    candidate table so the rejected sizes can be inspected."""
    fitlog.begin_page("p1")
    fitlog.record_block((0, 0, 100, 200), "That constitution of yours",
                        (0, 0, 100, 200), 60, 200, 60, 200, chosen=10,
                        largest_fitting=13, avail_used=True, n_lines=3)
    fitlog.record_candidates(
        [{"size": 13, "n": 3, "lone": 2, "tw": 116, "th": 47, "fill": 0.22,
          "reject": "wordlist(2/3)"}],
        chosen=10, largest_fitting=13, text="That constitution of yours",
        avail_used=True)
    blocks = [r for r in _lines(logging_on) if r["kind"] == "block"]
    cands = [r for r in _lines(logging_on) if r["kind"] == "candidates"]
    assert blocks[0]["chosen"] == 10 and blocks[0]["largest_fitting"] == 13
    assert cands[0]["table"][0]["reject"] == "wordlist(2/3)"


def test_summary_line_reaches_the_app_log(logging_on, caplog):
    """The per-page summary is what makes this visible in the Logs tab."""
    import logging
    fitlog.begin_page("00008.jpg")
    fitlog.record_region((0, 0, 10, 10), "container", (0, 0, 10, 10))
    fitlog.record_block((0, 0, 10, 10), "hi", (0, 0, 10, 10), 8, 8, 8, 8, 9, 14, True, 2)
    with caplog.at_level(logging.INFO, logger="mangafill.fit"):
        fitlog.end_page()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("FITLOG page=00008.jpg" in m for m in msgs), msgs
    # the summary must carry BOTH halves of the story
    line = next(m for m in msgs if "FITLOG page=00008.jpg" in m)
    assert "blocks=1" in line and "regions=1" in line and "under_sized=1" in line


def test_end_page_resets_so_pages_do_not_accumulate(logging_on):
    """Regions are recorded before typeset runs, so the reset must live in end_page —
    a mid-page clear silently discards the region records (the 'regions=0' bug)."""
    fitlog.begin_page("p1")
    fitlog.record_region((0, 0, 1, 1), "container", (0, 0, 1, 1))
    fitlog.record_block((0, 0, 1, 1), "t", (0, 0, 1, 1), 1, 1, 1, 1, 9, 9, True, 1)
    fitlog.end_page()
    assert fitlog._page["blocks"] == 0 and fitlog._page["branches"] == {}
    # a second page starts clean
    fitlog.set_page_label("p2")
    fitlog.record_block((0, 0, 1, 1), "t", (0, 0, 1, 1), 1, 1, 1, 1, 9, 9, True, 1)
    assert fitlog._page["blocks"] == 1


def test_never_raises_when_the_dump_path_is_unwritable(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("")
    monkeypatch.setattr(fitlog, "SENTINEL", str(sentinel))
    monkeypatch.setattr(fitlog, "JSONL", "/proc/nonexistent/dump.jsonl")
    fitlog.begin_page("x")
    fitlog.record_region((0, 0, 1, 1), "container", (0, 0, 1, 1))  # must not raise
    fitlog.record_block((0, 0, 1, 1), "t", (0, 0, 1, 1), 1, 1, 1, 1, 9, 9, True, 1)
    fitlog.end_page()
