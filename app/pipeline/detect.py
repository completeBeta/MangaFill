"""Text detection — PP-OCRv5 server det (ONNX) + classical CV safety net.

Manga text is predominantly VERTICAL, high-contrast, and inside bubbles. The
document-tuned PP-OCRv4 det (RapidOCR) missed stylised action text and free-floating
handwritten lines, so the neural detector is upgraded to **PP-OCRv5_server_det**
(Apache-2.0, PP-HGNetV2 backbone) running via onnxruntime — no PaddlePaddle native
inference (which is broken on CPU here). A classical CV pass with a vertical-line
kernel stays as a cheap safety net for vertical columns the neural detector still
misses (e.g. free-floating editorial text).

  PP-OCRv5_server_det boxes ∪ CV boxes → greedy NMS (larger boxes win).

Returns (x, y, w, h) boxes. manga-ocr OCRs each crop afterwards.
"""
from __future__ import annotations

import cv2
import numpy as np

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        # Lazy import: paddleocr pulls in paddlepaddle (~2-3s import). The ONNX
        # engine means inference runs on onnxruntime, not the broken PaddlePaddle CPU
        # native path.
        from paddleocr import TextDetection

        _engine = TextDetection(model_name="PP-OCRv5_server_det", engine="onnxruntime")
    return _engine


def _ppocrv5_boxes(image: np.ndarray) -> list[tuple]:
    result = _get_engine().predict(image, batch_size=1)
    polys = result[0].json["res"].get("dt_polys", [])
    out: list[tuple] = []
    for p in polys:
        xs = [pt[0] for pt in p]
        ys = [pt[1] for pt in p]
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
    boxes += _ppocrv5_boxes(image)
    boxes += _cv_boxes(image, ksize=(1, 25))  # vertical manga columns
    # NOTE: a horizontal kernel was tried and rejected — it slices vertical text
    # into horizontal strips (noise). Horizontal text (titles/watermarks) is
    # intentionally left out: titles stay as-is, watermarks are skipped.
    return _nms(boxes)
