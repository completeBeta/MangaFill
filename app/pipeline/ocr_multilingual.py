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

# A box reading below this confidence with a tall-narrow (vertical) aspect is
# treated as a missed vertical line and re-read after a 90° rotation.
_VERTICAL_CONF_FLOOR = 0.5

# A box smaller than this in BOTH dimensions is a candidate foliage/texture
# false positive. On dense artwork (tree canopies, grass, clouds) the detector
# fires on leaf clusters and the recognizer reads them as a single hanzi at
# moderate confidence (业 at 0.707, 义 at 0.860). But real small text exists too
# (single-char SFX 嗝 at 0.949, a lone 这 at 1.000), and it reads at HIGH
# confidence — so size alone must NOT drop a box; only size + low confidence is
# noise. Tuned for ~1600px manhua/webtoon pages.
_MIN_TEXT_DIM = 55
_NOISE_CONF = 0.9


def is_noise_box(w: int, h: int, conf: float | None = None) -> bool:
    """True if a detected box is small *and* low-confidence (foliage/texture).

    Small-but-confident boxes (real single-char SFX) are kept; only the
    combination of tiny AND weakly-recognized is treated as artwork noise.
    """
    if not (w < _MIN_TEXT_DIM and h < _MIN_TEXT_DIM):
        return False
    if conf is None:
        return False  # unknown confidence — never drop (missing text is worse)
    return float(conf) < _NOISE_CONF

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


def _avg_conf(boxes: list[tuple]) -> float:
    """Mean recognition confidence over `read_boxes_text` output, or 0.0."""
    return sum(c for _b, _t, c in boxes) / len(boxes) if boxes else 0.0


def read_boxes_text(image: Image.Image, lang: str) -> list[tuple]:
    """Detect + recognize text on a page. Returns [(x, y, w, h), text, conf].

    One full PaddleOCR pass (detection + orientation + recognition) over the
    page. `dt_polys` are the ORIGINAL detection boxes (the text region in the
    source image, before any orientation rotation) — those are what typeset /
    inpaint need. Vertical lines are still reported at their true (tall-narrow)
    source box; only the recognition is run on a rotated copy internally.

    Vertical-column fallback: when a tall-narrow box reads weakly (conf below
    ``_VERTICAL_CONF_FLOOR``), the textline-orientation classifier has likely
    missed a vertical line — the crop is rotated 90° and re-read so the
    recognizer sees it horizontally. The better read wins, mapped back to the
    ORIGINAL box coordinates.
    """
    arr = np.asarray(image.convert("RGB"))
    H, W = arr.shape[:2]
    out: list[tuple] = []
    for r in _pipeline(lang).predict(arr):
        for poly, text, conf in zip(r["dt_polys"], r["rec_texts"], r["rec_scores"]):
            text = (text or "").strip()
            if not text:
                continue  # garbage/empty line (wrong-script output scores ~0)
            x, y, w, h = _polys_to_xywh(poly)
            if w <= 0 or h <= 0:
                continue
            conf = float(conf)
            if conf < _VERTICAL_CONF_FLOOR and h > w * 1.5:
                text, conf = _reocr_rotated(arr, x, y, w, h, text, conf, lang)
            if is_noise_box(w, h, conf):
                continue  # foliage/texture false positive (tiny + low-conf)
            out.append(((x, y, w, h), text, conf))
    return out


def _reocr_rotated(
    arr: np.ndarray,
    x: int, y: int, w: int, h: int,
    text: str, conf: float, lang: str,
) -> tuple[str, float]:
    """Re-read a weakly-recognized vertical column after a 90° rotation.

    PaddleOCR's textline-orientation classifier handles most vertical text but
    misses stylized/decorative vertical lines, returning garbage at low
    confidence. Rotating the tall-narrow crop 90° turns the column into a
    horizontal line the recognizer reads reliably. Returns the better of the
    two reads (text + confidence). Keeps the ORIGINAL box — callers only need
    the corrected text, not a new region.
    """
    H, W = arr.shape[:2]
    pad = 8  # give the detector context around a tight text box
    x0, y0 = max(0, int(x) - pad), max(0, int(y) - pad)
    x1, y1 = min(W, int(x + w) + pad), min(H, int(y + h) + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return text, conf  # too small to rotate meaningfully
    crop = arr[y0:y1, x0:x1]
    # PIL rotates counter-clockwise: a top-to-bottom column becomes a
    # left-to-right line (correct reading order for CJK vertical text).
    rot = np.asarray(Image.fromarray(crop).rotate(90, expand=True).convert("RGB"))
    for rr in _pipeline(lang).predict(rot):
        for _p, rtext, rconf in zip(rr["dt_polys"], rr["rec_texts"], rr["rec_scores"]):
            rtext = (rtext or "").strip()
            if rtext and float(rconf) > conf:
                text, conf = rtext, float(rconf)
    return text, conf


def _drop(lang: str) -> None:
    """Free a cached pipeline we no longer need (memory-constrained hosts)."""
    import gc

    if _pipelines.pop(lang, None) is not None:
        gc.collect()


def drop_all_pipelines() -> None:
    """Free every cached PaddleOCR pipeline (used when the worker takes over)."""
    import gc

    if _pipelines:
        _pipelines.clear()
        gc.collect()


def detect_language(image: Image.Image) -> str:
    """Auto-detect the source language (ja/ko/zh) from a page.

    PaddleOCR's recognition is per-script: `ch` reads hanzi AND kana (Chinese
    and Japanese share one model); `korean` reads hangul only. On the wrong
    script a model emits garbage at ~0.0 confidence, so:

      * `ch` output carries kana (a Japanese-only marker)      -> Japanese
      * else `ch` output is hanzi without kana, read confidently-> Chinese
      * else `korean` output carries hangul, read confidently   -> Korean
      * no signal (blank / already-English page)                -> Japanese
        (the manga-ocr path, whose `_has_japanese` filter drops English).

    `ch` is probed FIRST — one pass classifies both CJK scripts — and `korean`
    is probed only when `ch` finds no CJK. This avoids running the korean
    recognizer on dense Chinese/Japanese pages, where its language-agnostic
    detection floods the page with hundreds of false-positive boxes and wedges
    the memory-constrained host (4 GB, no swap) — the `source_lang=auto` hang.

    Done once per job on the first page and cached for the whole run. The
    unneeded pipeline is dropped afterwards so we hold only the winning
    recognizer for the rest of the job, not both.
    """
    ch_boxes = read_boxes_text(image, "ch")
    ch_text = "".join(t for _b, t, _c in ch_boxes)
    ch_conf = _avg_conf(ch_boxes)

    if has_kana(ch_text):
        _drop("ch")  # ja routes to manga-ocr, not PaddleOCR
        return "ja"
    if has_hanzi(ch_text) and ch_conf > 0.4:
        return "zh"  # keep the ch recognizer for the job

    # No CJK signal: blank / already-English page, or Korean (ch can't read
    # hangul). Only now pay for the korean recognizer.
    _drop("ch")
    ko_boxes = read_boxes_text(image, "ko")
    ko_text = "".join(t for _b, t, _c in ko_boxes)
    ko_conf = _avg_conf(ko_boxes)
    _drop("ko")
    if has_hangul(ko_text) and ko_conf > 0.4:
        return "ko"
    return "ja"
