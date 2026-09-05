"""Multilingual OCR via EasyOCR (Apache-2.0) for Korean and Chinese.

manga-ocr (see `ocr.py`) is Japanese-only — it's the best tool for JP manga but
cannot read hangul or recognise pure-hanzi text reliably. EasyOCR is the
permissive, torch-based fallback for the other source languages. It runs on CPU
(the GPU worker only carries manga-ocr for now) and handles the horizontal text
that dominates Korean webtoons and Chinese manhua speech bubbles.

Detection + recognition happen in one pass (`readtext`); vertical text is a
known weaker case (EasyOCR is horizontal-tuned) — a future refinement.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

import easyocr

# easyocr language codes (paired with 'en' so English loanwords still OCR).
_READER_LANGS = {"ko": ["ko", "en"], "zh": ["ch_sim", "en"], "ja": ["ja", "en"]}

_readers: dict[str, easyocr.Reader] = {}


def _reader(lang: str) -> easyocr.Reader:
    if lang not in _readers:
        _readers[lang] = easyocr.Reader(_READER_LANGS[lang], gpu=False, verbose=False)
    return _readers[lang]


def read_boxes_text(image: Image.Image, lang: str) -> list[tuple]:
    """Detect + recognize text on a page. Returns [(x, y, w, h), text, conf]."""
    arr = np.asarray(image.convert("RGB"))
    results = _reader(lang).readtext(arr, detail=1, paragraph=False)
    out = []
    for (box, text, conf) in results:
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        if x1 <= x0 or y1 <= y0:
            continue
        out.append(((x0, y0, x1 - x0, y1 - y0), (text or "").strip(), float(conf)))
    return out


def detect_language(image: Image.Image) -> str:
    """Auto-detect the source language (ja/ko/zh) by OCR confidence.

    Each EasyOCR reader is trained on one script; run on the wrong language it
    still emits that script's characters but at near-zero confidence (the Korean
    model will "read" hangul into Chinese text, but with conf ~0.1 vs ~0.6 for
    real Korean). The highest average confidence therefore identifies the
    language — unlike a pure character-range check, which the hallucinating
    wrong-language model defeats. Done once per job, so three probes are cheap.
    """
    best_lang, best_conf = "ja", -1.0
    for lang in ("ko", "zh", "ja"):
        boxes = read_boxes_text(image, lang)
        confs = [c for _b, _t, c in boxes if c > 0.0]
        if not confs:
            continue
        avg = sum(confs) / len(confs)
        if avg > best_conf:
            best_lang, best_conf = lang, avg
    return best_lang
