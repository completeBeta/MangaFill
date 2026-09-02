"""End-to-end page render: detect → OCR → translate → inpaint → typeset.

Text and bubble detection are done by the trained ogkalu RT-DETR-v2 detector
(`detector.detect_containers`), which returns `bubble` (typeset target),
`text_bubble` (dialogue inside a bubble), and `text_free` (text outside any
bubble: narration boxes, handwritten mutters, SFX) in one pass.

Both `text_bubble` AND `text_free` are OCR'd + translated + re-lettered — the
only `text_free` skipped is pure-ASCII watermarks / page numbers (no kana/kanji).
Each translated block is typeset into its parent bubble (inset to its inscribed
rectangle) or, for free text with no bubble, into its own box.

If the detector is unavailable, it falls back to PP-OCRv5 + white-flood-fill.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .bubble import find_container, is_free_floating
from .detector import detect_containers, find_parent_bubble
from .ingest import load_image
from .inpaint import inpaint_text
from .ocr import ocr_crop
from .pipeline import process_page
from .translate import translate_page
from .types import TextBlock
from .typeset import typeset_page


def _has_japanese(text: str) -> bool:
    """True if `text` has kana/kanji/CJK punct — skips watermarks & page numbers."""
    return any(
        ("\u3040" <= ch <= "\u30ff")       # hiragana + katakana
        or ("\u4e00" <= ch <= "\u9fff")     # kanji
        or ("\u3000" <= ch <= "\u303f")     # CJK punctuation (。「」…)
        for ch in text
    )


def _iou(a: tuple, b: tuple) -> float:
    """Intersection-over-union of two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def render_translated_page(
    image_path: str,
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    dry_run: bool = False,
    font_id: str | None = None,
) -> tuple[Image.Image, list[TextBlock], int, int]:
    """Run the full pipeline on one page.

    Returns (result PIL image, blocks with translations, prompt_tokens,
    completion_tokens) — the token counts let the caller price the page.

    `dry_run=True` runs detect + OCR but skips the LLM translation (no API call,
    no cost) — the page is returned unchanged with blocks carrying empty
    translations, so a dry-run job records what was *found* without spending.
    """
    image = Image.fromarray(load_image(image_path))
    image_np = np.asarray(image)

    # Prefer the trained detector for text + bubble regions.
    det = None
    try:
        det = detect_containers(image)
    except Exception:
        det = None

    if det is not None:
        blocks: list[TextBlock] = []
        seen: list[tuple] = []
        # 1) dialogue inside bubbles
        for (x, y, w, h) in det["text_bubble"]:
            if any(_iou((x, y, w, h), s) > 0.5 for s in seen):
                continue  # detector double-fired the same region
            text, conf = ocr_crop(image_np, (x, y, w, h))
            if not text or len(text.strip()) <= 1:
                continue
            blocks.append(TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                                    orientation="vertical"))
            seen.append((x, y, w, h))
        # 2) free text (narration boxes, mutters, SFX) — translate it too
        for (x, y, w, h) in det["text_free"]:
            if any(_iou((x, y, w, h), s) > 0.5 for s in seen):
                continue  # dupe of a bubble or another free-text box
            text, conf = ocr_crop(image_np, (x, y, w, h))
            if not text or not _has_japanese(text):
                continue  # watermark / page number
            blocks.append(TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                                    orientation="vertical"))
            seen.append((x, y, w, h))
        # reading order: top-to-bottom, then right-to-left within a row
        blocks.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    else:
        blocks = process_page(image_path)

    if dry_run:
        pt = ct = 0
        for b in blocks:
            b.translation = ""
    else:
        blocks, pt, ct = translate_page(blocks, model, api_key, base_url)

    targets: list[tuple[TextBlock, tuple]] = []
    erase: list[tuple] = []
    if det is not None:
        for b in blocks:
            if not b.translation:
                continue
            region = find_parent_bubble(det["bubble"], b.bbox)
            if region is not None:
                # Inset the bubble's bounding box to approximate its inscribed
                # rectangle — ovals/spiked bubbles are narrower at the edges, so
                # fitting text to the full bbox spills over the outline.
                x, y, w, h = region
                ix, iy = int(w * 0.15), int(h * 0.12)
                region = (x + ix, y + iy, w - 2 * ix, h - 2 * iy)
            else:
                # Free text / caption: no bubble edge to avoid — use its own box.
                region = b.bbox
            targets.append((b, region))
            erase.append(b.bbox)
    else:
        gray = np.asarray(image.convert("L"))
        for b in blocks:
            if b.orientation == "furigana":
                erase.append(b.bbox)
                continue
            if not b.translation:
                continue
            if is_free_floating(gray, b.bbox):
                continue
            targets.append((b, find_container(gray, b.bbox)))
            erase.append(b.bbox)

    inpainted = inpaint_text(image, erase) if erase else image
    only = {id(b) for b, _region in targets}
    regions = {id(b): region for b, region in targets if region is not None}
    result = typeset_page(inpainted, blocks, font_id=font_id, regions=regions, only=only)
    return result, blocks, pt, ct
