"""Wiring test for the erase path in `render_translated_page`.

Rationale: the 2026-09-16 art-preserving erase shipped with a bug that every
pure-function unit test missed — `gray_page = np.asarray(image.convert("L")) if
image.ndim == 3` (a PIL `Image` has no `.ndim`). It only surfaced when a real page
was rendered in the browser on the test instance. This test drives the whole
function with the models stubbed out and asserts what the inpainter receives.

Geometry notes: the smooth gradient matters (a synthetic sawtooth gradient puts
high-contrast edges into the ink mask and the erase falls back to the whole box),
and Japanese pages pass through `_drop_titles`, which discards blocks taller than
~15% of the page — so the big-box case runs through the Korean branch.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from app.pipeline import render as R


def _page_with_drawn_sfx(path: str, h: int, w: int, stroke: tuple) -> None:
    """A soft gradient (so a tile-median background is needed) + one thick stroke."""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    for y in range(h):
        v = max(70, 235 - y // 10)
        d.line([(0, y), (w - 1, y)], fill=(v, v, v))
    x, y, sw, sh = stroke
    d.rectangle([x, y, x + sw, y + sh], fill=(25, 25, 25))
    im.save(path)


class _Stubs:
    """Stubs the model stages and records the erase boxes handed to the inpainter."""

    def __init__(self, box: tuple, det_box: tuple | None = None):
        self.erase = None
        self.block_box = box
        self.det_box = det_box or box

    def install(self, monkeypatch):
        def fake_inpaint(image, boxes, *a, **k):
            self.erase = list(boxes)
            return image

        def fake_translate(blocks, *a, **k):
            # the target loop only erases blocks that carry a translation
            for b in blocks:
                b.translation = "HELLO"
            return blocks, 0, 0

        monkeypatch.setattr(R, "inpaint_text", fake_inpaint)
        monkeypatch.setattr(R, "typeset_page", lambda image, *a, **k: image)
        monkeypatch.setattr(R, "translate_page", fake_translate)
        monkeypatch.setattr(R, "ocr_crop", lambda _np, _box: ("こんにちは", 0.99))
        monkeypatch.setattr(
            R, "detect_containers",
            lambda _i: {"bubble": [], "text_bubble": [], "text_free": [self.det_box]},
        )
        monkeypatch.setattr(
            R, "read_boxes_text", lambda _img, _lang: [(self.block_box, "조용", 0.99, 0.0)]
        )
        return self


def _render(tmp_path, monkeypatch, *, page: tuple, stroke: tuple, box: tuple,
            lang: str, det_box: tuple | None = None, blank: bool = False):
    path = tmp_path / f"page_{lang}.png"
    if blank:
        Image.new("RGB", (page[1], page[0]), "white").save(path)
    else:
        _page_with_drawn_sfx(str(path), h=page[0], w=page[1], stroke=stroke)
    stubs = _Stubs(box, det_box).install(monkeypatch)
    if blank:
        monkeypatch.setattr(R, "detect_containers",
                            lambda _i: {"bubble": [], "text_bubble": [], "text_free": []})
    image, blocks, _pt, _ct, _carry = R.render_translated_page(
        str(path), model="m", api_key="k", lang=lang, dry_run=False
    )
    return image, blocks, stubs


# --- a big box sitting on artwork decomposes into stroke rects ----------------

def test_erase_uses_stroke_rects_for_a_big_block(tmp_path, monkeypatch):
    page, stroke, box = (1600, 690), (60, 620, 560, 70), (40, 300, 600, 500)
    _image, blocks, stubs = _render(tmp_path, monkeypatch, page=page, stroke=stroke,
                                    box=box, lang="ko")
    assert blocks, "the stub OCR returned one text region"
    assert stubs.erase, "the inpainter must be called with erase boxes"
    assert len(stubs.erase) > 1, "a 600x500 box on art must decompose into stroke rects"
    assert sum(w * h for _x, _y, w, h in stubs.erase) < 0.75 * (box[2] * box[3])


def test_ja_page_with_a_drawn_sfx_also_reaches_the_inpainter(tmp_path, monkeypatch):
    page, stroke, box = (3000, 690), (60, 620, 560, 70), (40, 300, 600, 400)
    _image, blocks, stubs = _render(tmp_path, monkeypatch, page=page, stroke=stroke,
                                    box=box, lang="ja")
    assert blocks
    assert stubs.erase, "the ja branch must build erase boxes too"


# --- small and degenerate cases ----------------------------------------------

def test_erase_keeps_a_small_block_as_one_rect(tmp_path, monkeypatch):
    page, stroke, box = (1600, 690), (60, 620, 560, 70), (300, 600, 70, 40)
    _image, blocks, stubs = _render(tmp_path, monkeypatch, page=page, stroke=stroke,
                                    box=box, lang="ja", det_box=box)
    assert blocks
    assert stubs.erase == [box]


def test_render_does_not_crash_on_an_empty_page(tmp_path, monkeypatch):
    _image, blocks, stubs = _render(tmp_path, monkeypatch, page=(1600, 690),
                                    stroke=(0, 0, 0, 0), box=(40, 300, 600, 500),
                                    lang="ja", blank=True)
    assert blocks == []
    assert stubs.erase is None
