"""v0.27.41 — LEGIBILITY FALLBACK: a block the configured face cannot letter legibly
gets a NARROWER bundled face instead of staying microscopic.

MEASURED DEFECT (2026-09-22)
The job-3 character sheet carries stat cells like 'Defense Technique: B+' in a 140x26
box. Anime Ace — the default face and the WIDEST in the catalog — reaches 8px there;
Comic Neue reaches 12px and Patrick Hand 11px in the SAME box, with no layout change.
Across 30 under-legible blocks on 7 pages the configured face reached `_LEGIBLE_FLOOR`
on 0 of them, Comic Neue on 21 (mean +5px).

The rules these tests pin:
  * the configured face ALWAYS wins where it is already legible (byte-identical output);
  * the fallback only ever REPLACES a block that was illegible, and only when it is
    strictly bigger in the same box;
  * the lettering still stays inside its box (the fallback cannot cause a spill);
  * drawn SFX keeps the configured display face (it is art, not prose);
  * the resolver can never hand back the configured face itself.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline import fitlog, typeset
from app.pipeline.fonts import (font_path as catalog_font_path, resolve_font_path,
                                resolve_legibility_fallback)
from app.pipeline.types import TextBlock
from app.pipeline.typeset import _LEGIBLE_FLOOR, _draw_box, _fit

PRIMARY = resolve_font_path("anime-ace") or resolve_font_path(None)
FALLBACK = resolve_legibility_fallback(PRIMARY, "anime-ace")
STAT = "Defense Technique: B+"
STAT_BOX = (140, 26)
PAD = 20


def ink_bbox(img: Image.Image):
    """Bounding box of the dark glyph pixels on a white page (the halo is white)."""
    a = np.asarray(img.convert("L"))
    dark = a < 128
    if not dark.any():
        return None
    ys, xs = np.where(dark)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def draw(box, text, fallback=None, font=None):
    w, h = box
    img = Image.new("RGB", (w + 2 * PAD, h + 2 * PAD), "white")
    d = ImageDraw.Draw(img)
    _draw_box(img, d, (PAD, PAD, w, h), text, font or PRIMARY, 32,
              fallback_font_path=fallback)
    return img


@pytest.fixture()
def records(tmp_path, monkeypatch):
    """Capture the fitlog JSONL so a test can read the DECISION (size + face)."""
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("")
    jsonl = tmp_path / "trace.jsonl"
    monkeypatch.setattr(fitlog, "SENTINEL", str(sentinel))
    monkeypatch.setattr(fitlog, "JSONL", str(jsonl))
    monkeypatch.setattr(fitlog, "_meta_written", False)

    def _read():
        if not jsonl.exists():
            return []
        return [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]

    fitlog.reset()
    yield _read
    fitlog.reset()


def blocks_from(records):
    return [r for r in records() if r.get("kind") == "block"]


def test_the_configured_face_cannot_letter_this_block_legibly():
    """The premise: on the real stat cell the default face lands under the floor."""
    got = _fit(STAT, *STAT_BOX, PRIMARY, max_font=32)
    assert got is not None
    assert got[0] < _LEGIBLE_FLOOR, (
        f"premise changed: {PRIMARY} now reaches {got[0]}px in a {STAT_BOX} box")


def test_a_narrower_face_makes_an_illegible_block_legible(records):
    if not FALLBACK:
        pytest.skip("no bundled fallback face available in this checkout")
    plain = draw(STAT_BOX, STAT)
    rescued = draw(STAT_BOX, STAT, fallback=FALLBACK)

    b_plain, b_rescued = ink_bbox(plain), ink_bbox(rescued)
    assert b_plain and b_rescued
    assert (b_rescued[3] - b_rescued[1]) > (b_plain[3] - b_plain[1]), \
        "the fallback face did not draw bigger glyphs in the same box"

    recs = blocks_from(records)
    by_fb = {r.get("fallback"): r for r in recs}
    assert by_fb.get(True) is not None, "the rescued block was not recorded as a fallback"
    assert by_fb.get(False) is not None, "the configured-face block is missing from the trace"
    assert by_fb[True]["chosen"] > by_fb[False]["chosen"]
    assert by_fb[True]["face"] and by_fb[True]["face"] != by_fb[False]["face"]


def test_a_legible_block_is_left_exactly_as_it_was(records):
    """The configured face wins wherever it is already legible — byte for byte."""
    box, text = (320, 130), "Yes."
    plain = draw(box, text)
    with_fallback = draw(box, text, fallback=FALLBACK)
    assert np.array_equal(np.asarray(plain), np.asarray(with_fallback)), \
        "the fallback changed a block that was already legible"
    assert all(r.get("fallback") is False for r in blocks_from(records))


def test_the_lettering_stays_inside_its_box():
    """A bigger face must not spill: the fit still owns the geometry."""
    if not FALLBACK:
        pytest.skip("no bundled fallback face available in this checkout")
    for text, box in [(STAT, STAT_BOX), ("Yes.", (43, 30)), ("Hm?", (28, 33)),
                      ("Financial Power: D+", (126, 29))]:
        b = ink_bbox(draw(box, text, fallback=FALLBACK))
        assert b is not None, f"nothing was drawn for {text!r}"
        assert b[0] >= PAD - 1 and b[1] >= PAD - 1, f"{text!r} leaked off its box"
        assert b[2] <= PAD + box[0] + 1 and b[3] <= PAD + box[1] + 1, \
            f"{text!r} leaked past its box"


def test_no_fallback_face_available_changes_nothing(records):
    plain = draw(STAT_BOX, STAT)
    none_available = draw(STAT_BOX, STAT, fallback=None)
    assert np.array_equal(np.asarray(plain), np.asarray(none_available))
    assert all(r.get("fallback") is False for r in blocks_from(records))


def test_the_resolver_never_returns_the_configured_face():
    comic = catalog_font_path("comic-neue")
    patrick = catalog_font_path("patrick-hand")
    if comic:
        got = resolve_legibility_fallback(comic, "comic-neue")
        assert got != comic, "resolver returned the configured face itself"
    if patrick:
        got = resolve_legibility_fallback(patrick, "patrick-hand")
        assert got != patrick
    # and it never invents a path that is not on disk
    assert resolve_legibility_fallback(None, None) in (None, comic, patrick)


def test_drawn_sfx_keeps_the_configured_display_face(records):
    """SFX is art, lettered in the configured face on purpose — never the prose fallback."""
    img = Image.new("RGB", (200, 160), "white")
    blk = TextBlock(bbox=(20, 20, 128, 24), text="ドン", translation="BOOM!",
                    confidence=0.9, orientation="horizontal", is_sfx=True)
    typeset.typeset_page(img, [blk], font_path=PRIMARY, page_label="sfx")
    recs = blocks_from(records)
    assert recs, "the SFX block produced no record"
    assert all(r.get("fallback") is False for r in recs), \
        "SFX was lettered with the prose fallback face"
