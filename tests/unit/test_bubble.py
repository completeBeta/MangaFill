"""Unit tests for bubble-aware typesetting helpers (synthetic, no model loading)."""
import numpy as np

from app.pipeline.bubble import find_container, find_speech_box, is_free_floating


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


def test_find_container_relaxed_accepts_wide_webtoon_box():
    # A wide white box (3.5x the text width) is a normal webtoon speech box, not
    # a leak — the relaxed guards used by the ko/zh path accept it so English
    # lettering sizes up into the full box instead of the tight OCR region.
    gray = _image()
    gray[100:200, 60:200] = 255                         # white box 140x100
    _draw_glyphs(gray, 80, 110, 40, 40)                 # text 40x40 inside
    c = find_container(gray, (80, 110, 40, 40), max_width_ratio=4.0, max_height_ratio=3.0)
    assert c is not None
    cx, cy, cw, ch = c
    assert cw >= 100 and ch >= 80                       # captured most of the box


def test_find_speech_box_coloured_box():
    # A flat-coloured (non-white) speech box: find_speech_box samples the fill
    # colour and flood-fills it, recovering the box where the white-only
    # find_container would miss.
    rgb = np.full((300, 300, 3), 120, dtype=np.uint8)   # grey art
    rgb[100:200, 80:220] = (60, 120, 220)               # blue speech box (RGB)
    rgb[130:170, 120:180] = (20, 20, 20)                # dark text 60x40 inside
    c = find_speech_box(rgb, (120, 130, 60, 40))        # tight text bbox
    assert c is not None
    cx, cy, cw, ch = c
    assert cw >= 100 and ch >= 80                       # recovered most of the box


def test_find_balloon_gap_tolerant_recovers_tall_balloon_with_interior_art():
    """A tall balloon with a dark art blob in its lower half: the plain white flood
    stops at the art, but the gap-tolerant scan bridges it and recovers the full span."""
    import numpy as np
    from app.pipeline.bubble import find_balloon_gap_tolerant
    # white page, tall balloon x[100..300] y[50..500]. The interior art (a chibi) is
    # NOT a solid block — it has white gaps between its strokes, which is exactly
    # what the gap tolerance bridges (each dark run is <= gap px).
    g = np.full((600, 400), 255, dtype=np.uint8)
    g[0:50, :] = 30           # page background above balloon
    g[500:600, :] = 30        # below balloon
    g[:, 0:100] = 30; g[:, 300:400] = 30
    # chibi strokes: several thin dark bands with white between them
    for yy in range(300, 480, 18):
        g[yy:yy + 10, 120:280] = 30
    block = (150, 60, 100, 260)  # text column filling most of the balloon's height
    res = find_balloon_gap_tolerant(g, block)
    assert res is not None
    # recovers a span taller than the block (bridges the chibi strokes)
    assert res[3] > block[3]
    # encloses the block vertically
    assert res[1] <= block[1] and res[1] + res[3] >= block[1] + block[3]


def test_find_balloon_gap_tolerant_returns_none_for_free_text_on_art():
    """Free-floating text over dark artwork (no enclosing white balloon) -> None."""
    import numpy as np
    from app.pipeline.bubble import find_balloon_gap_tolerant
    g = np.full((600, 400), 40, dtype=np.uint8)  # all dark artwork
    block = (150, 60, 100, 200)
    assert find_balloon_gap_tolerant(g, block) is None


def test_find_balloon_gap_tolerant_rejects_page_wide_span():
    """A span that runs the full page height is the page background, not a balloon."""
    import numpy as np
    from app.pipeline.bubble import find_balloon_gap_tolerant
    g = np.full((600, 400), 255, dtype=np.uint8)  # all white
    block = (150, 60, 100, 200)
    # span reaches both page edges -> rejected
    assert find_balloon_gap_tolerant(g, block) is None
