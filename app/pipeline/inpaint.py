"""Inpaint — erase original Japanese text with LaMa (Apache-2.0).

Loads big-lama via simple-lama-inpainting. Its `pillow<10` metadata pin is stale —
it runs fine on pillow>=10 (verified). Mask = solid dilated boxes; LaMa reconstructs
the erased region (bubble interior -> white, artwork -> plausible fill).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

_lama = None


def _get_lama():
    global _lama
    if _lama is None:
        from simple_lama_inpainting import SimpleLama  # lazy: downloads big-lama on first use

        _lama = SimpleLama()
    return _lama


def _mask_from_boxes(size: tuple[int, int], boxes: list[tuple], dilate: int = 5) -> Image.Image:
    """Binary mask (255 = inpaint) covering each box, dilated to catch full strokes."""
    w, h = size
    m = np.zeros((h, w), dtype=np.uint8)
    for (x, y, bw, bh) in boxes:
        x0 = max(0, x - dilate)
        y0 = max(0, y - dilate)
        x1 = min(w, x + bw + dilate)
        y1 = min(h, y + bh + dilate)
        m[y0:y1, x0:x1] = 255
    return Image.fromarray(m, "L")


def inpaint_text(image: Image.Image, boxes: list[tuple], dilate: int = 5) -> Image.Image:
    """Erase text inside `boxes` from `image`. Returns a new RGB image."""
    lama = _get_lama()
    mask = _mask_from_boxes(image.size, boxes, dilate)
    return lama(image.convert("RGB"), mask).convert("RGB")
