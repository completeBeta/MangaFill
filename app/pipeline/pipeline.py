"""Orchestrate one page: ingest → detect → OCR → merge → list[TextBlock].

Headless — no FastAPI imports, so it's testable without a server.

Manga text is predominantly VERTICAL (縦書き), and the detector splits a single
vertical line into per-column boxes (right-to-left). We re-merge columns that
belong to the same line (vertically overlapping + horizontally adjacent), join
their text right-to-left, and sort the final blocks top-to-bottom / right-to-left.
"""
from __future__ import annotations

from .detect import detect_boxes
from .ingest import load_image
from .ocr import ocr_crop
from .types import TextBlock, polygon_to_bbox


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

        # within a line, read right-to-left (x descending)
        group.sort(key=lambda b: -b.bbox[0])
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
    for box in boxes:
        bbox = polygon_to_bbox(box)
        text, conf = ocr_crop(image, bbox)
        x, y, w, h = bbox
        orientation = "vertical" if h > w * 1.5 else "horizontal"
        blocks.append(
            TextBlock(box=box, bbox=bbox, text=text, confidence=conf, orientation=orientation)
        )

    merged = _merge_vertical_lines(blocks)
    # reading order: top-to-bottom, then right-to-left within a row
    merged.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    return merged
