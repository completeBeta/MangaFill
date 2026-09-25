"""Orchestrate one page: ingest → detect → OCR → merge → list[TextBlock].

Headless — no FastAPI imports, so it's testable without a server.

Detection (see detect.py) returns boxes; manga-ocr OCRs each crop. Blocks are
classed vertical / horizontal / furigana. Vertical columns the detector split are
re-merged right-to-left. Furigana (narrow ruby columns beside kanji) is kept
separate so it is not folded into the main text. Horizontal text (titles /
watermarks) is kept but flagged — it is not translated (titles stay as-is,
watermarks are skipped).
"""
from __future__ import annotations

from .detect import detect_boxes
from .ingest import load_image
from .ocr import ocr_crop
from .types import TextBlock


def _merge_vertical_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    """Merge adjacent vertical-text columns into single lines (RTL)."""
    if len(blocks) <= 1:
        return blocks

    merged: list[TextBlock] = []
    used = [False] * len(blocks)

    for i, bi in enumerate(blocks):
        if used[i]:
            continue
        xi, yi, wi, hi = bi.bbox
        group = [bi]
        used[i] = True

        for j, bj in enumerate(blocks):
            if used[j]:
                continue
            xj, yj, wj, hj = bj.bbox
            y_overlap = min(yi + hi, yj + hj) - max(yi, yj)
            if y_overlap > 0.5 * min(hi, hj):
                x_gap = max(xi, xj) - min(xi + wi, xj + wj)
                if x_gap < 0.6 * max(wi, wj):
                    group.append(bj)
                    used[j] = True

        group.sort(key=lambda b: -b.bbox[0])  # right-to-left
        text = "".join(b.text for b in group)
        xs = [b.bbox[0] for b in group]
        ys = [b.bbox[1] for b in group]
        x0 = min(xs)
        y0 = min(ys)
        x1 = max(b.bbox[0] + b.bbox[2] for b in group)
        y1 = max(b.bbox[1] + b.bbox[3] for b in group)
        merged.append(
            TextBlock(bbox=(x0, y0, x1 - x0, y1 - y0), text=text, orientation="vertical")
        )

    return merged


def process_page(image_path: str) -> list[TextBlock]:
    """Run detect → OCR → merge on a single manga page."""
    image = load_image(image_path)
    boxes = detect_boxes(image)

    blocks: list[TextBlock] = []
    for (x, y, w, h) in boxes:
        text, conf = ocr_crop(image, (x, y, w, h))
        if not text:
            continue
        if len(text) <= 1:
            # Isolated single kana = artwork noise (e.g. an eye OCR'd as "し"), not
            # dialogue. A real lone kana would be SFX, which v1 leaves as-is anyway.
            continue
        if w <= 16 and h > w * 2:
            orientation = "furigana"  # narrow ruby column beside kanji
        elif h > w * 1.5:
            orientation = "vertical"
        else:
            orientation = "horizontal"
        blocks.append(
            TextBlock(bbox=(x, y, w, h), text=text, confidence=conf, orientation=orientation)
        )

    furigana = [b for b in blocks if b.orientation == "furigana"]
    vertical = [b for b in blocks if b.orientation == "vertical"]
    horizontal = [b for b in blocks if b.orientation == "horizontal"]

    merged = _merge_vertical_lines(vertical)
    # reading order: top-to-bottom, then right-to-left within a row
    merged.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    furigana.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    horizontal.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    return merged + furigana + horizontal
