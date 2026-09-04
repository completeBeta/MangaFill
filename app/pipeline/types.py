"""Shared pipeline types."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextBlock:
    """A detected text region with its OCR result."""

    box: list = field(default_factory=list)   # 4-point polygon [[x,y], ...]
    bbox: tuple = (0, 0, 0, 0)                 # (x, y, w, h) axis-aligned crop
    text: str = ""                             # JP text (manga-ocr output)
    translation: str = ""                      # EN translation (LLM output)
    confidence: float | None = None
    orientation: str = "horizontal"            # horizontal | vertical | furigana


def polygon_to_bbox(box: list) -> tuple:
    """Axis-aligned (x, y, w, h) from a 4-point polygon."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
