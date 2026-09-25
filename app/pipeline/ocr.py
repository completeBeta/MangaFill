"""OCR — manga-ocr (Apache-2.0) on cropped text regions."""
from __future__ import annotations

import numpy as np
from PIL import Image
from manga_ocr import MangaOcr

from .device import get_device

_mocr: MangaOcr | None = None
_mocr_device: str | None = None


def _get_mocr() -> MangaOcr:
    global _mocr, _mocr_device
    device = get_device()
    if _mocr is None or _mocr_device != device:
        # manga-ocr runs on CUDA unless forced to CPU (force_cpu=True).
        _mocr = MangaOcr(force_cpu=(device == "cpu"))
        _mocr_device = device
    return _mocr


def ocr_crop(image: np.ndarray, bbox: tuple) -> tuple[str, float | None]:
    """OCR a cropped text region. Returns (jp_text, confidence).

    manga-ocr handles vertical (縦書き) text natively, so no rotation is needed.
    It returns no confidence score, so we return None.

    The detector occasionally returns a degenerate / out-of-bounds box (zero
    width/height, or fully off-page); cropping that yields an empty array, which
    crashes manga-ocr's ViT with "shape '[1, 3, 224, 224]' is invalid for input
    of size 0". Clamp to the image and treat a zero-area result as empty text so
    the caller drops the block instead of raising.
    """
    x, y, w, h = bbox
    H, W = image.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return "", None
    crop = Image.fromarray(image[y0:y1, x0:x1])
    text = _get_mocr()(crop)
    return (text or "").strip(), None
