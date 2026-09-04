"""End-to-end page render: detect → OCR → translate → inpaint → typeset.

Text and bubble detection are done by the trained ogkalu RT-DETR-v2 detector
(`detector.detect_containers`), which returns `bubble` (typeset target),
`text_bubble` (dialogue inside a bubble), and `text_free` (text outside any
bubble: narration boxes, handwritten mutters, SFX) in one pass.

Both `text_bubble` AND `text_free` are OCR'd. Vertical text (dialogue, vertical
name/caption columns) is always translated and re-lettered into its bubble or
box; horizontal text (stat lines, names, titles) is translated only on pages
that also carry vertical content, and is re-lettered at its own position so the
original layout is preserved. Pure-ASCII watermarks / page numbers are skipped.

If the detector is unavailable, it falls back to PP-OCRv5 + white-flood-fill.

When `gpu_worker_url` is configured, detect+OCR and inpaint are offloaded to the
GPU worker (see `remote.py`); any failure silently falls back to the local CPU
models, so a down GPU never breaks a job.
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
from .remote import remote_detect_ocr, remote_inpaint
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


def _drop_non_japanese(blocks: list[TextBlock]) -> list[TextBlock]:
    """Drop blocks whose OCR text isn't Japanese.

    Raw manga is kana/kanji; anything OCR'd back without Japanese is already-
    English content (a pre-translated page, an English stat line, a watermark).
    Those must be left byte-for-byte untouched — re-translating them re-letters
    English on top of English. Applied to the merged block list so it covers the
    remote GPU-worker path and the PP-OCR fallback too, not just the local
    detector.
    """
    return [b for b in blocks if b.text and _has_japanese(b.text)]


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


def _box_containment(a: tuple, b: tuple) -> float:
    """Fraction of the *smaller* box's area covered by the intersection.

    A nested detection (the detector emits the same text column at several
    granularities) has near-1.0 containment but low IoU, so IoU alone misses it.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    smaller = min(aw * ah, bw * bh)
    return inter / smaller if smaller else 0.0


def _dedup_boxes(boxes: list[tuple], seen: list[tuple], keep: str = "largest") -> list[tuple]:
    """Drop nested/overlapping detections.

    The ogkalu detector frequently returns the same text region at several
    granularities (a whole box plus its sub-lines). Processing all of them OCRs
    and typesets the same text repeatedly, which is the 'double-vision' on stat
    pages. Which box survives depends on the orientation:

      * ``keep="largest"`` — for *vertical* text, keep the full column (a name/
        caption column split into fragments should re-merge to one block).
      * ``keep="smallest"`` — for *horizontal* stat text, keep the individual
        lines (a whole stat box is a container; its lines are the real text).

    ``_box_containment`` is symmetric (fraction of the *smaller* box covered),
    so the same rejection test works in both directions — only the iteration
    order differs.
    """
    kept: list[tuple] = []
    ordered = sorted(boxes, key=lambda b: (b[2] * b[3]) if keep == "smallest" else -(b[2] * b[3]))
    for box in ordered:
        if any(_iou(box, k) > 0.5 or _box_containment(box, k) > 0.85 for k in seen + kept):
            continue
        kept.append(box)
    return kept


def _orientation(w: int, h: int) -> str:
    """Classify a free-text region's orientation from its box shape.

    Tall-narrow is a vertical name/caption column (translated); wide is a
    horizontal stat line / title / credit (left untouched). Speech-bubble text
    is handled separately and always forced vertical — its boxes span several
    vertical columns, giving a near-square aspect that this shape test would
    mis-read as horizontal.
    """
    if w <= 16 and h > w * 2:
        return "furigana"
    if h > w * 1.5:
        return "vertical"
    return "horizontal"


def _build_blocks_from_det(det: dict, image_np: np.ndarray) -> list[TextBlock]:
    """OCR the detector's `text_bubble` + `text_free` regions into TextBlocks."""
    blocks: list[TextBlock] = []
    seen: list[tuple] = []
    # 1) dialogue inside bubbles — always vertical, keep the full region
    for (x, y, w, h) in _dedup_boxes(det["text_bubble"], seen, keep="largest"):
        text, conf = ocr_crop(image_np, (x, y, w, h))
        if not text or len(text.strip()) <= 1 or not _has_japanese(text):
            continue  # already-English / non-JP bubble — leave untouched
        blocks.append(TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                                orientation="vertical"))
        seen.append((x, y, w, h))
    # 2) free text — split by shape, dedup each group with the right strategy:
    #    vertical columns keep the full region; horizontal stat lines keep the
    #    individual lines (a whole stat box is a container, not a line).
    free = det["text_free"]
    free_vertical = [(x, y, w, h) for (x, y, w, h) in free if _orientation(w, h) != "horizontal"]
    free_horizontal = [(x, y, w, h) for (x, y, w, h) in free if _orientation(w, h) == "horizontal"]
    for (x, y, w, h) in _dedup_boxes(free_vertical, seen, keep="largest"):
        text, conf = ocr_crop(image_np, (x, y, w, h))
        if not text or not _has_japanese(text):
            continue  # watermark / page number
        blocks.append(TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                                orientation=_orientation(w, h)))
        seen.append((x, y, w, h))
    for (x, y, w, h) in _dedup_boxes(free_horizontal, seen, keep="smallest"):
        text, conf = ocr_crop(image_np, (x, y, w, h))
        if not text or not _has_japanese(text):
            continue  # watermark / page number
        blocks.append(TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                                orientation="horizontal"))
        seen.append((x, y, w, h))
    # reading order: top-to-bottom, then right-to-left within a row
    blocks.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    return blocks


def _is_blank(image_np: np.ndarray) -> bool:
    """True if the page is essentially empty (a blank/divider page)."""
    gray = image_np.astype(float).mean(axis=2) if image_np.ndim == 3 else image_np
    return float(gray.mean()) > 250.0


def _is_color(image: Image.Image) -> bool:
    """True if the page has real colour (a colour splash/cover, not B/W manga).

    The translate → inpaint → typeset pipeline is built for B/W manga; running it
    on a colour cover mangles the artwork. Chroma = max(R,G,B) - min(R,G,B) per
    pixel; a real colour page averages well above ~10 while B/W manga stays < 3.
    """
    a = np.asarray(image.convert("RGB")).astype(float)
    chroma = a.max(axis=2) - a.min(axis=2)
    return float(chroma.mean()) > 10.0


def render_translated_page(
    image_path: str,
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    dry_run: bool = False,
    font_id: str | None = None,
    gpu_worker_url: str = "",
    progress_cb=None,
) -> tuple[Image.Image, list[TextBlock], int, int]:
    """Run the full pipeline on one page.

    Returns (result PIL image, blocks with translations, prompt_tokens,
    completion_tokens) — the token counts let the caller price the page.

    `dry_run=True` runs detect + OCR but skips the LLM translation (no API call,
    no cost) — the page is returned unchanged with blocks carrying empty
    translations, so a dry-run job records what was *found* without spending.

    `gpu_worker_url`, when set, offloads detect+OCR and inpaint to the remote
    GPU worker; failures fall back to the local CPU models.

    `progress_cb(stage)`, when provided, is called with one of ``detect``,
    ``ocr``, ``translate``, ``inpaint``, ``typeset`` as the page advances through
    the pipeline — the worker uses it to drive a finer-grained progress bar than
    whole-page granularity.
    """
    emit = progress_cb or (lambda _stage: None)

    image = Image.fromarray(load_image(image_path))
    image_np = np.asarray(image)

    # Blank and colour pages (dividers, covers, colour splashes) are left
    # byte-for-byte unchanged — there's no dialogue to translate, and running
    # the B/W pipeline on them erases logo art or produces blank output.
    if _is_blank(image_np) or _is_color(image):
        return image, [], 0, 0

    # ---- detect + OCR (remote GPU worker → local detector → PP-OCRv5) --------
    bubbles = None
    blocks = None
    if gpu_worker_url:
        try:
            emit("detect")
            remote = remote_detect_ocr(image, gpu_worker_url)
            bubbles = remote["bubble"]
            blocks = [
                TextBlock(bbox=tuple(b["bbox"]), text=b["text"], confidence=None,
                          orientation=b.get("orientation", "vertical"))
                for b in remote["blocks"]
            ]
        except Exception:
            bubbles = None
            blocks = None

    if blocks is None:
        det = None
        try:
            emit("detect")
            det = detect_containers(image)
        except Exception:
            det = None
        if det is not None:
            bubbles = det["bubble"]
            emit("ocr")
            blocks = _build_blocks_from_det(det, image_np)
        else:
            bubbles = None
            emit("ocr")
            blocks = process_page(image_path)

    # Drop already-English text (pre-translated pages, English stat lines,
    # watermarks) so it is left byte-for-byte untouched — never re-translated or
    # re-lettered on top of existing English. Covers the remote-worker path too,
    # which returns blocks without this filter.
    blocks = _drop_non_japanese(blocks)

    # ---- translate (cloud LLM) ----------------------------------------------
    if dry_run:
        pt = ct = 0
        for b in blocks:
            b.translation = ""
    else:
        emit("translate")
        # Translate horizontal stat text only on pages that also carry vertical
        # content (stat/character pages) — a pure-horizontal page (cover/credit
        # page with only titles + credits) is left as-is.
        has_vertical = any(b.orientation == "vertical" for b in blocks)
        blocks, pt, ct = translate_page(
            blocks, model, api_key, base_url, translate_horizontal=has_vertical
        )

    # ---- resolve typeset targets + erase boxes -------------------------------
    targets: list[tuple[TextBlock, tuple]] = []
    erase: list[tuple] = []
    if bubbles is not None:
        for b in blocks:
            if not b.translation:
                continue
            if b.orientation == "horizontal":
                # Horizontal stat line / name: typeset at its own box to preserve
                # the original layout (do NOT center into a shared parent bubble —
                # that's what stacked the two-column stat tables on top of each other).
                region = b.bbox
            else:
                region = find_parent_bubble(bubbles, b.bbox)
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

    # ---- inpaint (remote GPU worker → local LaMa) ----------------------------
    if erase:
        emit("inpaint")
        if gpu_worker_url:
            try:
                inpainted = remote_inpaint(image, erase, gpu_worker_url)
            except Exception:
                inpainted = inpaint_text(image, erase)
        else:
            inpainted = inpaint_text(image, erase)
    else:
        inpainted = image

    only = {id(b) for b, _region in targets}
    regions = {id(b): region for b, region in targets if region is not None}
    emit("typeset")
    result = typeset_page(inpainted, blocks, font_id=font_id, regions=regions, only=only)
    return result, blocks, pt, ct
