"""Unit tests for bubble-aware typesetting helpers (synthetic, no model loading)."""
import numpy as np

from app.pipeline.bubble import find_container, is_free_floating


def _image(w=400, h=400, bg=128):
    return np.full((h, w), bg, dtype=np.uint8)


def _draw_glyphs(gray, x, y, w, h, bg=255):
    """Sparse dark 'glyph' strokes inside a text bbox, leaving mostly white bg."""
    gray[y : y + h, x : x + w] = bg
    # a few thin vertical strokes (like vertical JP kana columns)
    for dx in (4, 10, 16, 22):
        if x + dx + 2 < x + w:
            gray[y + 5 : y + h - 5, x + dx : x + dx + 2] = 0


def test_find_container_white_bubble():
    gray = _image()
    gray[110:260, 100:200] = 255                      # white bubble (100x150)
    _draw_glyphs(gray, 120, 140, 60, 90)              # text inside (60x90)
    c = find_container(gray, (120, 140, 60, 90))
    assert c is not None
    cx, cy, cw, ch = c
    assert cx <= 120 and cy <= 140
    assert cx + cw >= 120 + 60 and cy + ch >= 140 + 90
    assert cw <= 110 and ch <= 160                    # stays within the bubble


def test_find_container_free_floating_none():
    gray = _image()                                   # grey (screentone) bg
    _draw_glyphs(gray, 130, 140, 40, 60, bg=140)      # text on grey, no white
    assert find_container(gray, (130, 140, 40, 60)) is None


def test_is_free_floating_screentone_vs_bubble():
    # screentone grey background -> free-floating
    gray = _image(bg=140)
    _draw_glyphs(gray, 130, 140, 40, 60, bg=140)
    assert is_free_floating(gray, (130, 140, 40, 60)) is True

    # white bubble background -> not free-floating
    gray = _image(bg=255)
    _draw_glyphs(gray, 130, 140, 40, 60, bg=255)
    assert is_free_floating(gray, (130, 140, 40, 60)) is False


def test_find_container_rejects_boundary_leak():
    # All-white image: any flood fill leaks to the boundary -> rejected.
    gray = _image(bg=255)
    _draw_glyphs(gray, 130, 140, 40, 60, bg=255)
    assert find_container(gray, (130, 140, 40, 60)) is None


def test_find_container_rejects_oversized():
    # A huge white region relative to the text (5x) is treated as a leak.
    gray = _image()
    gray[50:350, 50:350] = 255
    _draw_glyphs(gray, 130, 140, 40, 60)
    assert find_container(gray, (130, 140, 40, 60)) is None
