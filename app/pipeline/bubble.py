"""Bubble detection — recover the white speech-bubble / box enclosing a text block.

Detection produces tight VERTICAL text columns; English is horizontal and needs
the bubble's full interior width. This module seeds a white pixel just outside
the text bbox and flood-fills the connected white component (bounded to a window)
to recover the bubble region for typesetting.

Free-floating text (e.g. editorial teasers over screentone/artwork, which have no
clean white container) returns None so callers leave the original text untouched.
"""
from __future__ import annotations

import cv2
import numpy as np


def find_container(
    gray: np.ndarray,
    bbox: tuple,
    margin: int = 140,
    thresh: int = 215,
    min_area_ratio: float = 1.15,
    min_overlap: float = 0.6,
    max_width_ratio: float = 2.5,
    max_height_ratio: float = 1.8,
) -> tuple | None:
    """Return (x, y, w, h) of the white container enclosing `bbox`, else None.

    `bbox` is the tight text column (x, y, w, h). The container is the white
    bubble/box interior. Guards reject false hits:
      * area >= min_area_ratio * text area (a real bubble, not just the outline),
      * container bbox overlaps >= min_overlap of the text bbox (text sits inside
        the bubble, not off in a big leaked white region),
      * container width/height <= max_*_ratio * text width/height. "Bleeding"
        bubbles (whose white merges with the panel gutter, e.g. Bleach's big
        shout bubble) flood-fill far past the real bubble; the tight height/width
        caps make them fall back to the text bbox instead of overflowing.
    """
    x, y, w, h = bbox
    H, W = gray.shape
    if w <= 0 or h <= 0:
        return None

    x0 = max(0, x - margin)
    x1 = min(W, x + w + margin)
    y0 = max(0, y - margin)
    y1 = min(H, y + h + margin)
    sub = gray[y0:y1, x0:x1]
    white = (sub >= thresh).astype(np.uint8)

    cx, cy = x + w // 2, y + h // 2
    seeds = []
    for (sx, sy) in (
        (cx, y - 4),
        (cx, y + h + 4),
        (x - 4, cy),
        (x + w + 4, cy),
        (x + 2, y + 2),
        (x + w - 2, y + h - 2),
    ):
        lx, ly = sx - x0, sy - y0
        if 0 <= lx < white.shape[1] and 0 <= ly < white.shape[0]:
            seeds.append((lx, ly))

    n, labels, stats, _cen = cv2.connectedComponentsWithStats(white, 8)
    text_area = w * h

    for (lx, ly) in seeds:
        if white[ly, lx] == 0:
            continue
        lab = int(labels[ly, lx])
        if lab <= 0:
            continue
        rx, ry, rw, rh, area = stats[lab]
        if area < 30:
            continue
        if rw * rh < min_area_ratio * text_area:
            continue
        if rw > max_width_ratio * w or rh > max_height_ratio * h:
            continue
        # Reject containers that run to the image boundary: a flood fill that
        # reaches the page margin has leaked into the gutter, not a real bubble.
        gx, gy = rx + x0, ry + y0
        if gx <= 1 or gy <= 1 or gx + rw >= W - 1 or gy + rh >= H - 1:
            continue
        # overlap of text bbox with container bbox (in window coords)
        ox = max(0, min(x + w, gx + rw) - max(x, gx))
        oy = max(0, min(y + h, gy + rh) - max(y, gy))
        if ox * oy < min_overlap * text_area:
            continue
        return (int(gx), int(gy), int(rw), int(rh))
    return None


def is_free_floating(gray: np.ndarray, bbox: tuple, thresh: int = 215, min_white: float = 0.60) -> bool:
    """True if the text sits on a non-white background (screentone/artwork).

    Free-floating editorial/handwritten text has no clean white bubble: the
    fraction of white pixels inside its tight bbox is low (screentone is grey),
    unlike dialogue which sits on a white bubble interior. Callers leave such
    text untouched rather than drawing English over the artwork.
    """
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return False
    region = gray[y : y + h, x : x + w]
    return float((region >= thresh).mean()) < min_white
