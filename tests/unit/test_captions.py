#!/usr/bin/env python3
"""In-world caption recovery (`app.pipeline.captions`) — unit tests.

The feature exists to narrow the horizontal-text gate WITHOUT re-introducing the
v0.16.1/v0.16.2 damage (logos erased, credits mangled, hallucinated titles), so the
tests are mostly about what it must REFUSE to touch:

  * candidate rules  — credits/colophon wording, long text, vertical text and
                       latin signage never reach the model;
  * clustering       — one caption split into several OCR boxes is read as one crop;
  * measurement      — the lettered/erased box is the caption's own band, so a
                       decorative rule underneath it survives, and the measured glyph
                       height yields a physical capacity;
  * capacity         — a hallucinated reading longer than the box can hold is
                       rejected (p159: 27 glyphs in a 426x37 box);
  * the driver       — a TITLE/CREDITS verdict, an unusable translation or a provider
                       error all leave the page exactly as it was.

No test may touch the network: `read_caption` is monkeypatched everywhere.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline import captions as C
from app.pipeline.translate import ProviderError
from app.pipeline.types import TextBlock


# --------------------------------------------------------------------------- helpers

def _caption_page(w=1200, h=400):
    """A fixture with the REAL geometry of job-3 p159.

    Five 64px glyphs where the caption sits (x 723.., y 211..275) and a decorative
    18px band below it (y 285..303) — the band is the thing that must survive, so the
    lettered block has to be the caption's own band, not the padded union. The page is
    sized like a real one because `measure_text_box` measures the ink it is given.
    """
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    x = 723
    for _ in range(5):                      # caption glyphs, 64px tall
        d.rectangle([x, 211, x + 55, 275], fill="black")
        x += 70
    for i in range(20):                     # decorative band, 18px tall
        bx = 699 + i * 22
        d.rectangle([bx, 285, bx + 14, 303], fill="black")
    return img, np.asarray(img.convert("L"))


def _blk(x, y, w, h, text="", orientation="horizontal", translation=""):
    return TextBlock(box=[[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                     bbox=(x, y, w, h), text=text, translation=translation,
                     orientation=orientation)


P159_FRAGMENTS = [  # the real boxes from the prod DB for job 3 page 00159
    (723, 211, 124, 64, "【給仕"),
    (854, 215, 62, 53, "キ"),
    (699, 271, 426, 37, "つまりますのですかもしれませんでしょうか"),
]


# ------------------------------------------------------------------- candidate rules

def test_credits_and_colophon_never_qualify():
    """The pages the gate exists to protect: cover credits, colophon, titles."""
    blocks = [
        _blk(0, 0, 100, 40, "原作"),                       # credit marker
        _blk(0, 50, 100, 40, "●木野コトラ"),                 # credit bullet
        _blk(0, 100, 300, 40, "２０２２年１１月３０日電子販発行"),  # too long
        _blk(0, 150, 100, 40, "MangaStone.com"),           # latin signage
        _blk(0, 200, 60, 200, "エリス！"),                    # vertical dialogue
        _blk(0, 400, 100, 40, "給仕"),                       # already translated
    ]
    blocks[-1].translation = "Server."
    assert C.caption_candidates(blocks) == []


def test_real_caption_fragments_qualify():
    """All three real boxes qualify as GEOMETRY — including the 27-character junk box,
    which is the only thing covering the `ーマ` half of the caption. Acceptance is
    decided later by the vision read plus `capacity()`."""
    cands = C.caption_candidates([_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS])
    assert [c.text for c in cands] == ["【給仕", "キ", "つまりますのですかもしれませんでしょうか"]


def test_a_paragraph_is_not_a_candidate():
    """Prose must never be sent to the model (or replaced by a caption block)."""
    long_line = "これはとても長い説明文であって読み物の本文でありまして吹き出しではありませんので対象外です" * 2
    assert C.caption_candidates([_blk(0, 0, 900, 60, long_line)]) == []


def test_cluster_of_prose_is_skipped(monkeypatch):
    """A cluster whose combined reading is prose (over CLUSTER_MAX_CHARS) never reaches
    the model — the guard is on the cluster, not on each fragment."""
    img, _ = _caption_page()
    para = "これは長い説明文のつもりでありまして本文の一部でありますところの文章でございます"
    blocks = [_blk(40, 30, 200, 60, para), _blk(250, 30, 200, 60, para)]
    assert sum(len(b.text) for b in blocks) > C.CLUSTER_MAX_CHARS
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return {"kind": "CAPTION", "jp": "本文", "en": "Body"}, 1, 1

    monkeypatch.setattr(C, "read_caption", _spy)
    out, _pt, _ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                       base_url="http://x/v1")
    assert called["n"] == 0 and len(out) == 2


def test_credit_marker_matching_is_case_insensitive_for_latin():
    assert C.has_credit_marker("(C) 2022 Publisher")
    assert C.has_credit_marker("原作：あずみ圭")
    assert not C.has_credit_marker("【給仕 キーマ】")


# -------------------------------------------------------------------------- clustering

def test_one_caption_split_into_three_boxes_becomes_one_cluster():
    """p159 arrived as three boxes and must be read as ONE crop, otherwise the name
    half of 【給仕 キーマ】 is never in the crop the vision model sees."""
    groups = C.cluster_candidates([_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS])
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_distant_candidates_are_not_merged():
    groups = C.cluster_candidates([
        _blk(10, 10, 100, 40, "給仕"),
        _blk(10, 900, 100, 40, "メイド"),
    ])
    assert len(groups) == 2


# ------------------------------------------------------------------ ink + measurement

def test_measure_text_box_excludes_the_decorative_band():
    """The lettered/erased box is the caption's own band: the decorative rule below it
    (y 285+) must stay outside, or erasing the caption would damage the artwork."""
    _img, gray = _caption_page()
    tight, glyph_h = C.measure_text_box(gray, (699, 199, 438, 121))
    assert 55 <= glyph_h <= 75, glyph_h            # the caption's glyph height
    assert tight[1] + tight[3] < 285, tight        # stops above the band
    assert tight[1] >= 200 and tight[1] <= 215, tight


def test_capacity_accepts_the_real_caption_and_rejects_the_hallucination():
    """The arithmetic that replaces a confidence score on the ja path (manga-ocr emits
    none at all, so a threshold is not available)."""
    # the caption's own band on prod: 438x72 at glyph height 64
    assert C.capacity(438, 72, 64) >= C.width_glyphs("【給仕 キーマ】")
    assert C.width_glyphs("【給仕 キーマ】") == 5          # brackets/space take no advance
    # the hallucination's own box: 426x37 -> a 20-glyph reading cannot fit
    assert C.capacity(426, 37, 37) < C.width_glyphs("つまりますのですかもしれませんでしょ")


def test_width_glyphs_ignores_brackets_and_spaces():
    assert C.width_glyphs("【給仕 キーマ】") == 5
    assert C.width_glyphs("「あ」") == 1


def test_capacity_never_returns_zero_for_tiny_regions():
    assert C.capacity(4, 4, 4) >= 3


# --------------------------------------------------------------------------- driver

def test_no_candidates_means_no_work_and_no_tokens():
    img, _ = _caption_page()
    out, pt, ct = C.recover_captions(img, [_blk(0, 0, 100, 40, "原作")],
                                     model="m", api_key="k", base_url="http://x/v1")
    assert pt == ct == 0 and len(out) == 1


def test_non_caption_verdict_leaves_the_page_untouched(monkeypatch):
    img, _ = _caption_page()
    blocks = [_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS]
    monkeypatch.setattr(C, "read_caption",
                        lambda *a, **k: ({"kind": "TITLE", "jp": "何か", "en": "Something"}, 1, 1))
    out, pt, ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                     base_url="http://x/v1")
    assert len(out) == 3 and all(not b.translation for b in out)
    assert pt == 1 and ct == 1


def test_hallucinated_length_is_rejected(monkeypatch):
    """The vision model echoes junk: the arithmetic guard must still refuse it."""
    img, _ = _caption_page()
    blocks = [_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS]
    monkeypatch.setattr(C, "read_caption", lambda *a, **k: (
        {"kind": "CAPTION", "jp": "つまりますのですかもしれませんでしょうかとおもいますがどうでしょう",
         "en": "Long hallucination"}, 1, 1))
    out, _pt, _ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                       base_url="http://x/v1")
    assert len(out) == 3 and all(not b.translation for b in out)


def test_credit_reading_is_rejected_even_with_a_caption_verdict(monkeypatch):
    img, _ = _caption_page()
    blocks = [_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS]
    monkeypatch.setattr(C, "read_caption", lambda *a, **k: (
        {"kind": "CAPTION", "jp": "原作：あずみ圭", "en": "Original work"}, 1, 1))
    out, _pt, _ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                       base_url="http://x/v1")
    assert len(out) == 3 and all(not b.translation for b in out)


def test_accepted_caption_replaces_the_fragments(monkeypatch):
    """The happy path: three junk boxes collapse into ONE lettered block whose box is
    the caption's own band (not the padded union that would swallow the band below)."""
    img, _ = _caption_page()
    blocks = [_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS]
    monkeypatch.setattr(C, "read_caption", lambda *a, **k: (
        {"kind": "CAPTION", "jp": "【給仕 キーマ】", "en": "[SERVER: KEEMA]"}, 10, 5))
    out, pt, ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                     base_url="http://x/v1")
    assert pt == 10 and ct == 5
    assert len(out) == 1
    got = out[0]
    assert got.text == "【給仕 キーマ】" and got.translation == "[SERVER: KEEMA]"
    assert got.orientation == "horizontal"
    assert got.bbox[1] + got.bbox[3] < 285, got.bbox   # stops above the decorative band


def test_provider_error_leaves_everything_alone(monkeypatch):
    img, _ = _caption_page()
    blocks = [_blk(x, y, w, h, t) for x, y, w, h, t in P159_FRAGMENTS]

    def _boom(*a, **k):
        raise ProviderError("api.example.com timed out", transient=True)

    monkeypatch.setattr(C, "read_caption", _boom)
    out, pt, ct = C.recover_captions(img, blocks, model="m", api_key="k",
                                     base_url="http://x/v1")
    assert len(out) == 3 and all(not b.translation for b in out)
    assert pt == ct == 0


def test_json_parsing_tolerates_fences_and_prose():
    assert C._parse_json('```json\n{"kind": "CAPTION", "jp": "あ", "en": "A"}\n```')["kind"] == "CAPTION"
    assert C._parse_json('Sure!\n{"kind": "NONE"}\nHope that helps.')["kind"] == "NONE"
    assert C._parse_json("not json at all") == {}
    assert C._parse_json("") == {}
