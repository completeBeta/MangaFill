"""Unit tests for typeset: min-size fallback never leaves a blank bubble."""
import os

import numpy as np
from PIL import Image

from app.pipeline.typeset import typeset_page
from app.pipeline.types import TextBlock


def _font():
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return p
    return None


def test_long_translation_never_blank():
    fp = _font()
    if fp is None:
        return  # font unavailable on this host; skip
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    b = TextBlock(bbox=(90, 100, 20, 80), text="あ", translation="a very long english sentence that cannot possibly fit", orientation="vertical")
    out = typeset_page(img, [b], font_path=fp)
    # The translation must have been drawn (not skipped) even though it overflows.
    assert out != img


def test_furigana_and_empty_skipped():
    fp = _font()
    if fp is None:
        return
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    furi = TextBlock(bbox=(10, 10, 8, 30), text="かな", translation="reading", orientation="furigana")
    empty = TextBlock(bbox=(100, 10, 20, 30), text="", translation="", orientation="vertical")
    out = typeset_page(img, [furi, empty], font_path=fp)
    assert out == img  # nothing drawn


def test_text_has_white_outline_on_dark_background():
    fp = _font()
    if fp is None:
        return
    # Black background: the black fill is invisible, so any non-black pixels in
    # the box must come from the white outline added behind the glyphs.
    img = Image.new("RGB", (300, 200), (0, 0, 0))
    b = TextBlock(bbox=(50, 50, 200, 100), text="あ", translation="HELLO", orientation="vertical")
    out = typeset_page(img, [b], font_path=fp)
    arr = np.asarray(out.crop((50, 50, 250, 150)))
    white = int(((arr[:, :, 0] > 200) & (arr[:, :, 1] > 200) & (arr[:, :, 2] > 200)).sum())
    assert white > 0


def test_wrap_prefers_sentence_boundary(monkeypatch):
    import app.pipeline.typeset as t

    # Deterministic fake width = char count.
    monkeypatch.setattr(t, "_text_w", lambda draw, text, font: len(text))
    # "ONE. TWO THREE" is 14 chars > 10: greedy would break after "TWO", but the
    # sentence boundary after "ONE." is preferred.
    assert t._wrap(None, "ONE. TWO THREE", None, 10) == ["ONE.", "TWO THREE"]


def test_wrap_balances_to_avoid_lone_word(monkeypatch):
    import app.pipeline.typeset as t

    monkeypatch.setattr(t, "_text_w", lambda draw, text, font: len(text))
    # Greedy gives ["AAAA BBBB", "CC"]; the balance pass shifts "BBBB" down so
    # the short last line doesn't dangle a lone word.
    assert t._wrap(None, "AAAA BBBB CC", None, 10) == ["AAAA", "BBBB CC"]


def test_wrap_no_break_without_overflow(monkeypatch):
    import app.pipeline.typeset as t

    monkeypatch.setattr(t, "_text_w", lambda draw, text, font: len(text))
    assert t._wrap(None, "SHORT LINE", None, 50) == ["SHORT LINE"]


def test_wrap_word_wider_than_box_does_not_hang(monkeypatch):
    import app.pipeline.typeset as t

    monkeypatch.setattr(t, "_text_w", lambda draw, text, font: len(text))
    # "BBBBBBBB" (8 chars) exceeds max_w=5; the DP must still place it alone
    # (and not loop forever on an unbreakable position).
    assert t._wrap(None, "AA BBBBBBBB CC", None, 5) == ["AA", "BBBBBBBB", "CC"]
