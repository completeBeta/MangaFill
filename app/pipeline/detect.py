"""Text detection — hybrid ensemble (RapidOCR + classical CV).

Manga text is predominantly VERTICAL, high-contrast, and inside bubbles. A single
detector is not enough: the document-tuned PP-OCR det (RapidOCR) misses stylised
action text, while a *square* morphological kernel merges text into dark artwork.

So we ensemble:
  1. RapidOCR (PP-OCR det via onnxruntime) — standard text.
  2. Classical CV with a VERTICAL-line kernel (1,25) — vertical manga columns
     (catches text the doc detector misses, e.g. small/action text).
  3. Classical CV with a HORIZONTAL-line kernel (25,1) — horizontal text/titles.
  → union + greedy NMS (keep larger boxes; prefers merged CV lines over fragments).

Returns (x, y, w, h) boxes. manga-ocr OCRs each crop afterwards.
"""
from __future__ import annotations

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

_engine: RapidOCR | None = None


def _get_engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR(det_box_thresh=0.3)
    return _engine


def _rapidocr_boxes(image: np.ndarray) -> list[tuple]:
    result, _ = _get_engine()(image)
    out: list[tuple] = []
    for box, _text, _score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append(
            (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)))
        )
    return out


def _cv_boxes(image: np.ndarray, ksize: tuple, c: int = 15) -> list[tuple]:
    """Classical CV text-region detection via adaptive threshold + morphology."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, c
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, ksize)
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    n, _labels, stats, _cen = cv2.connectedComponentsWithStats(closed, 8)

    out: list[tuple] = []
    img_area = image.shape[0] * image.shape[1]
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 250:
            continue
        if area > img_area * 0.2:  # huge blob = dark artwork, skip
            continue
        if w < 10:  # thin marks = speed lines / artwork, not text columns
            continue
        out.append((int(x), int(y), int(w), int(h)))
    return out


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter == 0:
        return 0.0
    return inter / (aw * ah + bw * bh - inter)


def _nms(boxes: list[tuple], iou_thresh: float = 0.3) -> list[tuple]:
    """Greedy NMS — larger boxes first, so merged CV lines beat fragments."""
    boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple] = []
    for b in boxes:
        if all(_iou(b, k) < iou_thresh for k in kept):
            kept.append(b)
    return kept


def detect_boxes(image: np.ndarray) -> list[tuple]:
    """Return a deduplicated list of (x, y, w, h) text-region boxes."""
    boxes: list[tuple] = []
    boxes += _rapidocr_boxes(image)
    boxes += _cv_boxes(image, ksize=(1, 25))  # vertical manga columns
    # NOTE: a horizontal kernel was tried and rejected — it slices vertical text
    # into horizontal strips (noise). Horizontal text (titles/watermarks) is
    # intentionally left out: titles stay as-is, watermarks are skipped.
    return _nms(boxes)
