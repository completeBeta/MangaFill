"""OCR — manga-ocr (Apache-2.0) on cropped text regions."""
from __future__ import annotations

import numpy as np
from PIL import Image
from manga_ocr import MangaOcr

_mocr: MangaOcr | None = None


def _get_mocr() -> MangaOcr:
    global _mocr
    if _mocr is None:
        _mocr = MangaOcr()
    return _mocr


def ocr_crop(image: np.ndarray, bbox: tuple) -> tuple[str, float | None]:
    """OCR a cropped text region. Returns (jp_text, confidence).

    manga-ocr handles vertical (縦書き) text natively, so no rotation is needed.
    It returns no confidence score, so we return None.
    """
    x, y, w, h = bbox
    crop = Image.fromarray(image[y : y + h, x : x + w])
    text = _get_mocr()(crop)
    return (text or "").strip(), None
