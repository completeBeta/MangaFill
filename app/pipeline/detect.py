"""Text/balloon detection — RapidOCR (onnxruntime, Apache-2.0).

Returns 4-point polygons for text regions. manga-ocr then OCRs each crop.
RapidOCR's own recognition is ignored (it's Chinese-tuned); we use only its
DETECTION boxes and OCR with manga-ocr.
"""
from __future__ import annotations

import numpy as np
from rapidocr_onnxruntime import RapidOCR

_engine: RapidOCR | None = None


def _get_engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def detect_boxes(image: np.ndarray) -> list[list]:
    """Detect text regions and return their 4-point polygons."""
    result, _ = _get_engine()(image)
    return [box for box, _text, _score in result]
