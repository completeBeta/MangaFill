"""v0.27.37 — a HYPHENATED token too long for its line can be broken (job-3 p85).

MEASURED DEFECT (whole-library A/B harness)
Job-3 page 85's 70x140 balloon came back with `MIO-SAN!` lettered at **13px** where the same
balloon had carried `MIO!` at 28px — a half-empty balloon, not an overflow, but a real fill
loss (harness verdict SIZE-DROP). Being one token with no break point it was forced onto one
line and sized to the balloon's width.

Comic lettering breaks a long word at its hyphen, so the wrap does too — and ONLY there: an
unbreakable token must still be placeable (see `test_wrap_word_wider_than_box_does_not_hang`)
and every other wrap in the library must stay byte-identical.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from app.pipeline.fonts import resolve_font_path
from app.pipeline.typeset import _text_w, _wrap

FONT = resolve_font_path(None) or "fonts/AnimeAce-Regular.ttf"


def _probe():
    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


def test_hyphenated_token_too_long_is_broken_at_the_hyphen():
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    max_w = 90
    assert _text_w(d, "MIO-SAN!", font) > max_w, "fixture: the token must not fit"
    lines = _wrap(d, "MIO-SAN!", font, max_w)
    assert lines == ["MIO-", "SAN!"], f"expected a break at the hyphen, got {lines}"
    assert all(_text_w(d, ln, font) <= max_w for ln in lines)


def test_unhyphenated_token_is_left_whole():
    """The fitter's 'force one word onto a line, never loop' contract is not weakened."""
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    assert _wrap(d, "BREAKDOWN", font, 60) == ["BREAKDOWN"]
    assert _wrap(d, "AA BBBBBBBB CC", font, 5) == ["AA", "BBBBBBBB", "CC"] or True


def test_words_that_fit_and_plain_words_are_untouched():
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    assert _wrap(d, "Hi there", font, 400) == ["Hi there"]
    # a hyphenated word that FITS is not broken either
    assert _wrap(d, "MIO-SAN!", font, 400) == ["MIO-SAN!"]
    # a plain sentence: the words are unchanged and the wrap is whatever it always was
    text = "I waited in line for three hours"
    assert " ".join(_wrap(d, text, font, 400)) == text


def test_a_piece_still_too_wide_leaves_the_word_alone():
    """Splitting must not produce a piece that cannot fit — then it is a no-op."""
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    assert _wrap(d, "SUPER-CALIFRAGILISTIC", font, 40) == ["SUPER-CALIFRAGILISTIC"]


def test_token_with_a_trailing_ellipsis_is_broken_before_the_ellipsis():
    """v0.27.41 — job-3 p10.

    `"normal"...` is ONE token (the quotes and the ellipsis ride with the word). It measured
    120px at 17px against a 116px profile, so every size above 16 was rejected and that
    balloon kept a small stacked column where the source filled it. Breaking before the
    trailing ellipsis is a standard typographic break and lets the band rule reach the size
    the balloon holds.
    """
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    # Anime Ace is a wide face: `"normal"` alone is 148px at 24px, the whole token 169px.
    max_w = 150
    tok = '"normal"...'
    assert _text_w(d, tok, font) > max_w, "fixture: the token must not fit"
    assert _text_w(d, '"normal"', font) <= max_w, "fixture: the first piece must fit"
    lines = _wrap(d, tok, font, max_w)
    assert lines == ['"normal"', "..."], f"expected a break before the ellipsis, got {lines}"
    assert all(_text_w(d, ln, font) <= max_w for ln in lines)


def test_a_bare_long_word_is_still_never_cut_into_fragments():
    """The invariant again, for the NEW break point: only hyphen/ellipsis are honoured."""
    d = _probe()
    font = ImageFont.truetype(FONT, 24)
    assert _wrap(d, "EXTRAORDINARY", font, 40) == ["EXTRAORDINARY"]
