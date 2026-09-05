"""Multilingual OCR via PaddleOCR (PP-OCRv5/v6, Apache-2.0) for Korean + Chinese.

manga-ocr (see `ocr.py`) stays the Japanese engine — it is the best tool for
tategaki manga. PaddleOCR replaces EasyOCR for `ko` and `zh`: it reads VERTICAL
text natively (its textline-orientation classifier rotates vertical/rotated
lines to horizontal before recognition) and is measurably more accurate on
hangul than EasyOCR was (EasyOCR read the vertical 作揖 as a single garbled
character at 0.003 confidence; PaddleOCR reads it correctly).

Inference runs on CPU through the ONNX runtime engine — paddlepaddle 3.3.x
native CPU inference is broken (PIR/oneDNN crash), so the engine is pinned to
``onnxruntime`` (see references/ppocrv5-detection.md). The per-language
pipelines are lazy singletons; the recognition models are tiny (14-81 MB), so
CPU inference is a few seconds per page and does NOT need the GPU worker.

Language auto-detection uses the fact that PaddleOCR's recognition models are
per-script: ``korean`` reads hangul, ``ch`` reads hanzi AND kana (PP-OCRv5/v6
rec covers simplified+traditional Chinese and Japanese in one model). Run on the
wrong script each emits garbage at ~0.0 confidence, so a confidence comparison
plus a kana/hangul range check is unambiguous (see `detect_language`).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .language import has_hangul, has_hanzi, has_kana

# PaddleOCR language codes for the source languages we OCR here (ja stays on
# manga-ocr, but `ch` doubles as the CJK probe in `detect_language` because it
# reads Japanese kanji+kana too).
_PADDLE_LANGS = {"ko": "korean", "zh": "ch"}

_pipelines: dict[str, object] = {}


def _pipeline(lang: str):
    """Lazy singleton PaddleOCR pipeline for `lang` ('ko' | 'zh' | 'ch')."""
    if lang not in _pipelines:
        from paddleocr import PaddleOCR  # heavy import (~2-3s) — lazy on purpose

        _pipelines[lang] = PaddleOCR(
            lang=_PADDLE_LANGS.get(lang, lang),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,  # the vertical-text fix
            device="cpu",
            engine="onnxruntime",  # paddle native CPU inference is broken (PIR)
        )
    return _pipelines[lang]


def _polys_to_xywh(poly) -> tuple[int, int, int, int]:
    pts = np.asarray(poly)
    xs, ys = pts[:, 0], pts[:, 1]
    x0, y0 = int(xs.min()), int(ys.min())
    x1, y1 = int(xs.max()), int(ys.max())
    return x0, y0, x1 - x0, y1 - y0


def read_boxes_text(image: Image.Image, lang: str) -> list[tuple]:
    """Detect + recognize text on a page. Returns [(x, y, w, h), text, conf].

    One full PaddleOCR pass (detection + orientation + recognition) over the
    page. `dt_polys` are the ORIGINAL detection boxes (the text region in the
    source image, before any orientation rotation) — those are what typeset /
    inpaint need. Vertical lines are still reported at their true (tall-narrow)
    source box; only the recognition is run on a rotated copy internally.
    """
    arr = np.asarray(image.convert("RGB"))
    out: list[tuple] = []
    for r in _pipeline(lang).predict(arr):
        for poly, text, conf in zip(r["dt_polys"], r["rec_texts"], r["rec_scores"]):
            text = (text or "").strip()
            if not text:
                continue  # garbage/empty line (wrong-script output scores ~0)
            x, y, w, h = _polys_to_xywh(poly)
            if w <= 0 or h <= 0:
                continue
            out.append(((x, y, w, h), text, float(conf)))
    return out


def _drop(lang: str) -> None:
    """Free a cached pipeline we no longer need (memory-constrained hosts)."""
    import gc

    if _pipelines.pop(lang, None) is not None:
        gc.collect()


def detect_language(image: Image.Image) -> str:
    """Auto-detect the source language (ja/ko/zh) from a page.

    PaddleOCR's recognition is per-script: `korean` reads hangul, `ch` reads
    hanzi AND kana (Chinese and Japanese share one model). On the wrong script a
    model emits garbage at ~0.0 confidence, so:

      * `korean` reads hangul, confidently beating `ch` -> Korean
      * else `ch` output carries kana (a Japanese-only marker)   -> Japanese
      * else `ch` output is hanzi without kana, read confidently -> Chinese
      * no signal (blank / already-English page)                  -> Japanese
        (the manga-ocr path, whose `_has_japanese` filter drops English).

    Done once per job on the first page and cached for the whole run. The
    unneeded pipeline is dropped afterwards — the hosts are memory-constrained
    (4 GB, no swap), so we hold only the winning recognizer for the rest of the
    job, not both.
    """
    ko_boxes = read_boxes_text(image, "ko")
    ko_text = "".join(t for _b, t, _c in ko_boxes)
    ko_conf = sum(c for _b, _t, c in ko_boxes) / len(ko_boxes) if ko_boxes else 0.0
    _drop("ko")  # free the korean recognizer before loading ch — hold one at a time

    ch_boxes = read_boxes_text(image, "ch")
    ch_text = "".join(t for _b, t, _c in ch_boxes)
    ch_conf = sum(c for _b, _t, c in ch_boxes) / len(ch_boxes) if ch_boxes else 0.0

    if has_hangul(ko_text) and ko_conf > ch_conf:
        _drop("ch")  # caller reloads the korean recognizer for the job
        return "ko"
    if has_kana(ch_text):
        _drop("ch")  # ja routes to manga-ocr, not PaddleOCR
        return "ja"
    if has_hanzi(ch_text) and ch_conf > 0.4:
        return "zh"
    _drop("ch")
    return "ja"
