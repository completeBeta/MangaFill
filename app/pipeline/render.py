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

import re

import numpy as np
from PIL import Image

from .bubble import find_container, find_speech_box, is_free_floating, region_angle
from .detector import detect_containers, find_parent_bubble
from .ingest import load_image
from .inpaint import inpaint_text
from .language import has_cjk_or_hangul
from .ocr import ocr_crop
from .ocr_multilingual import detect_language, drop_all_pipelines, read_boxes_text, is_noise_box
from .pipeline import process_page
from .remote import remote_detect_ocr, remote_inpaint, remote_ocr_multilingual
from .translate import translate_page
from .types import TextBlock
from .typeset import typeset_page


def _has_japanese(text: str) -> bool:
    """True if `text` has kana/kanji/hangul/CJK punct — skips watermarks & page
    numbers and already-English text. (Name is historical: the gate now also
    accepts Korean hangul for multi-language input.)"""
    return has_cjk_or_hangul(text)


# PaddleOCR (ko/zh) returns a per-box recognition confidence. Legit dialogue
# reads ~0.9-1.0; misread SFX / decorative glyphs / garbled signs read below
# ~0.5 (e.g. 阿大奥色狂 0.37, 福 0.11, 房育院院司民房院房理司 0.47). Drop those so the
# original artwork is left untouched instead of being transliterated as nonsense.
# manga-ocr (ja) emits NO confidence (None), so this gate never touches Japanese.
_MIN_CONFIDENCE = 0.5


def _drop_low_confidence(conf) -> bool:
    """True if a recognition confidence is clearly a misread (SFX/decorative)."""
    return conf is not None and conf < _MIN_CONFIDENCE


# 第百五話 / 第105話 / 第1章 — a chapter/episode heading. This is a rock-solid
# marker that the page is a table of contents or a chapter-title page (cover and
# credit pages carry no chapter numbers), so its horizontal text is safe to
# translate.
_CHAPTER_HEADING_RE = re.compile(r"第[〇一二三四五六七八九十百千零0-9]+[話章回編節]")


def _has_chapter_heading(blocks) -> bool:
    """True if any block is a chapter/episode heading (第N話/章/回/編/節)."""
    return any(_CHAPTER_HEADING_RE.search(b.text or "") for b in blocks)


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


def _drop_corner_watermarks(blocks: list[TextBlock], page_w: int, page_h: int) -> list[TextBlock]:
    """Skip publisher/scan watermarks sitting in the extreme bottom corners.

    The `_has_japanese` gate already drops ASCII watermarks ("MangaStone.com"),
    but a Chinese manhua's publisher mark (腾讯动漫, 哔哩哔哩漫画) is hanzi and would
    otherwise be "translated" into nonsense ("Tencent Comics") over the logo. A
    publisher mark sits in the bottom ~6% of the page and hugs either edge; real
    story text (footnotes, captions) sits higher and more centrally.

    A bottom-corner narration CAPTION also hugs the left edge (its text is
    left-aligned inside a centered box), so the edge check alone wrongly drops
    it. A publisher mark is a narrow logo while a narration caption spans a much
    wider fraction of the page — require narrowness too, so wide captions are
    kept and only narrow corner marks are dropped.
    """
    if page_h <= 0:
        return blocks
    return [
        b for b in blocks
        if not (
            b.bbox[1] > 0.94 * page_h
            and (b.bbox[0] < 0.25 * page_w or b.bbox[0] + b.bbox[2] > 0.75 * page_w)
            and b.bbox[2] < 0.25 * page_w
        )
    ]


def _dedup_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Drop nested/overlapping OCR'd blocks so a region is typeset once.

    The remote GPU worker returns the same text at several granularities (a whole
    region plus its sub-regions / the same line OCR'd twice), which otherwise
    re-letters English on top of English. Keeps the largest block per cluster
    (most complete text) and drops anything that overlaps it. This is a safety
    net: the worker's own per-orientation dedup is the primary fix.
    """
    if len(blocks) <= 1:
        return blocks
    kept: list[TextBlock] = []
    for b in sorted(blocks, key=lambda x: -(x.bbox[2] * x.bbox[3])):
        if any(
            _iou(b.bbox, k.bbox) > 0.5 or _box_containment(b.bbox, k.bbox) > 0.85
            for k in kept
        ):
            continue
        kept.append(b)
    kept.sort(key=lambda b: (b.bbox[1], -b.bbox[0]))
    return kept


def _drop_titles(blocks: list[TextBlock], page_h: int) -> list[TextBlock]:
    """Skip large horizontal title/header text (series title, section headers,
    logos).

    manga-ocr misreads decorative title lettering (e.g. 月が導く異世界道中 OCR'd
    as 日道異世界中の建築), so "translating" it produces nonsense. Titles/headers
    are proper nouns / logos — leave them as-is. A block that is BOTH taller than
    ~15% of the page AND wider than it is tall is a large-font horizontal
    title/header; a tall-narrow bio paragraph or vertical name banner is kept.
    """
    if page_h <= 0:
        return blocks
    return [
        b for b in blocks
        if not (b.bbox[3] > 0.15 * page_h and b.bbox[2] > b.bbox[3])
    ]


def _split_bullet_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    """Split bullet-separated stat text (●筋力Ｂ＋●持久力Ｂ...) into per-line
    blocks so each stat typesets on its own line instead of wrapping as one
    paragraph. The sub-bbox divides the column evenly by line count (approximate
    — a two-column stat box becomes one stacked list, still far more readable)."""
    out: list[TextBlock] = []
    for b in blocks:
        if b.text.count("●") <= 1:
            out.append(b)
            continue
        parts = [p.strip() for p in b.text.split("●") if p.strip()]
        if len(parts) <= 1:
            out.append(b)
            continue
        x, y, w, h = b.bbox
        n = len(parts)
        for i, part in enumerate(parts):
            out.append(TextBlock(
                bbox=(x, y + int(i * h / n), w, max(1, int(h / n))),
                text=part,
                confidence=b.confidence,
                orientation="horizontal",
            ))
    return out


def _merge_horizontal_words(boxes: list) -> list:
    """Merge side-by-side word fragments on one line into a single box.

    PaddleOCR splits a spaced Korean line into one box per word (space-delimited),
    so a line like ``좋지 않아?`` comes back as ``좋지`` + ``않아?``. Words on the
    same line share a vertical band, are of similar height, and sit a small
    horizontal gap apart; a separate speech bubble sits at a larger gap or a
    different vertical band. Join adjacent same-line fragments left-to-right with
    a space so the line is translated as a unit instead of word-by-word.
    """
    if len(boxes) <= 1:
        return boxes
    ordered = sorted(boxes, key=lambda b: (b[0][1], b[0][0]))
    lines: list[dict] = []
    for (x, y, w, h), text, conf, angle in ordered:
        placed = False
        for ln in reversed(lines):
            bx, by, bw, bh = ln["bbox"]
            v_overlap = min(y + h, by + bh) - max(y, by)
            if v_overlap <= 0.5 * min(h, bh):
                continue  # different line (different vertical band)
            if min(h, bh) < 0.6 * max(h, bh):
                continue  # very different heights — not the same line
            gap = max(x, bx) - min(x + w, bx + bw)  # <0 if overlapping
            if gap > 0.5 * max(h, bh):
                continue  # too far apart — a different bubble
            ln["bbox"] = (min(bx, x), min(by, y),
                          max(bx + bw, x + w) - min(bx, x),
                          max(by + bh, y + h) - min(by, y))
            ln["members"].append((x, text, conf, angle, w))
            placed = True
            break
        if not placed:
            lines.append({"bbox": (x, y, w, h),
                          "members": [(x, text, conf, angle, w)]})
    out: list = []
    for ln in lines:
        members = sorted(ln["members"], key=lambda m: m[0])  # left-to-right
        text = " ".join(m[1] for m in members)
        conf = max(m[2] for m in members)
        aw = sum(m[3] * m[4] for m in members)
        ww = sum(m[4] for m in members)
        out.append((ln["bbox"], text, conf, aw / ww if ww else 0.0))
    out.sort(key=lambda b: (b[0][1], b[0][0]))
    return out


def _merge_stacked_lines(boxes: list) -> list:
    """Merge vertically-stacked OCR line fragments into single blocks.

    A multi-line speech box comes back from PaddleOCR as one box per line
    (e.g. a 4-line dialogue returns 4 stacked `(x, y, w, h)` boxes). Typesetting
    each fragment at its own tight box produces small, cramped lettering and
    misaligned translations. This groups vertically-adjacent, horizontally-
    overlapping fragments into one block (joining text top-to-bottom), so the
    whole bubble is translated and lettered as a unit. Horizontally separated
    columns (different bubbles) stay distinct.
    """
    if len(boxes) <= 1:
        return boxes
    ordered = sorted(boxes, key=lambda b: (b[0][1], b[0][0]))
    blocks: list[dict] = []
    for (x, y, w, h), text, conf, angle in ordered:
        placed = False
        for blk in reversed(blocks):
            bx, by, bw, bh = blk["bbox"]
            x_overlap = min(x + w, bx + bw) - max(x, bx)
            if x_overlap <= 0.3 * min(w, bw):
                continue  # different column — never merge across a horizontal gap
            y_gap = y - (by + bh)
            # A nested/duplicate detection (the same text region OCR'd twice,
            # or a sub-region of an already-seen block) sits almost ENTIRELY
            # inside the block — near-full containment. A tilted multi-line
            # bubble's lines overlap only PARTIALLY: their axis-aligned boxes
            # overlap heavily because of the slant, but the text is distinct.
            # So reject only near-full containment (a real duplicate) and merge
            # partial overlap (an adjacent line of the same tilted bubble).
            if _box_containment((x, y, w, h), (bx, by, bw, bh)) > 0.9:
                continue  # nested/duplicate detection — not a distinct line
            if y_gap > 0.6 * min(h, bh):
                continue  # separate bubble below (visible gap)
            nx = min(bx, x)
            ny = min(by, y)
            nx2 = max(bx + bw, x + w)
            ny2 = max(by + bh, y + h)
            blk["bbox"] = (nx, ny, nx2 - nx, ny2 - ny)
            blk["text"] += text
            blk["conf"] = max(blk["conf"], conf)
            blk["aw"] += angle * w  # width-weighted slant accumulator
            blk["ww"] += w
            placed = True
            break
        if not placed:
            blocks.append({"bbox": (x, y, w, h), "text": text, "conf": conf,
                           "aw": angle * w, "ww": w})
    # Reading order: top-to-bottom, then left-to-right.
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return [(b["bbox"], b["text"], b["conf"], b["aw"] / b["ww"]) for b in blocks]


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


def _overlaps_any(bbox: tuple, boxes: list[tuple]) -> bool:
    """True if `bbox` substantially overlaps any box (a nested/duplicate region,
    or a carryover region a previous page already drew)."""
    return any(_box_containment(bbox, q) > 0.5 or _iou(bbox, q) > 0.5 for q in boxes)


def _paste_carryover(result_np: np.ndarray, carryover: list) -> np.ndarray:
    """Paste carryover patches (a previous page's bubble bottom halves) onto a
    rendered page array. Each entry is ``(patch, (x, y, w, h))`` in this page's
    coordinates."""
    out = result_np.copy()
    for patch, (cx, cy, cw, ch) in carryover:
        out[cy:cy + ch, cx:cx + cw] = patch
    return out


def _extract_carryover(result_np: np.ndarray, targets: list, page_h: int) -> list:
    """Extract the below-boundary halves of bubbles that straddle `page_h`.

    Returns ``[(patch, (x, 0, w, h))]`` — each patch is the rendered bottom half
    of a boundary bubble, in the NEXT page's coordinates (y shifted to 0)."""
    patches: list = []
    for _b, region in targets:
        if region is None:
            continue
        rx, ry, rw, rh = region
        if ry < page_h < ry + rh:
            bottom = min(ry + rh, result_np.shape[0])
            patch = result_np[page_h:bottom, rx:rx + rw].copy()
            if patch.size:
                patches.append((patch, (rx, 0, rw, bottom - page_h)))
    return patches


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


def _inset_box(bbox: tuple, wx: float = 0.15, hy: float = 0.12) -> tuple:
    """Inset a bubble/box bbox to approximate its inscribed rectangle.

    Oval and spiked/starburst bubbles are narrower at the edges, so fitting text
    to the full bounding box spills over the outline (the "text overflowing the
    bubble" bug). Shrink by a fraction of each dimension — the same fraction the
    JA parent-bubble path uses — so lettering stays inside the drawn outline.
    Returns the original box if the inset would collapse it to nothing.
    """
    x, y, w, h = bbox
    ix, iy = int(w * wx), int(h * hy)
    nw, nh = w - 2 * ix, h - 2 * iy
    if nw < 8 or nh < 8:
        return bbox
    return (x + ix, y + iy, nw, nh)


def _expand_box(bbox: tuple, wx: float = 0.25, hy: float = 0.20) -> tuple:
    """Expand a tight OCR text bbox toward the enclosing speech box.

    When no clean speech box can be recovered (the flood-fill leaked into the
    white page background through a thin outline), the raw OCR box is much
    smaller than the real bubble and lettering comes out tiny. Grow it a modest
    fraction instead — a middle ground between the cramped OCR box and an
    unbounded guess that would spill into the artwork.
    """
    x, y, w, h = bbox
    ex, ey = int(w * wx), int(h * hy)
    return (max(0, x - ex), max(0, y - ey), w + 2 * ex, h + 2 * ey)


def _caption_region(bbox: tuple, page_w: int, page_h: int) -> tuple:
    """Region for free-floating text that has no enclosing speech box.

    English is horizontal, so a *vertical* caption (问世间情为何物) needs width, and
    a short-wide footnote needs height for its wrapped lines — but small on-screen
    labels / single characters must NOT be widened across the page (that spilled
    UI text into neighbouring panels). Only tall-narrow text gets the wide strip;
    everything else keeps its own width and just gains vertical room.
    """
    x, y, w, h = bbox
    if h > w * 1.5:
        # Vertical caption (问世间情为何物): English is horizontal, so give it a
        # strip whose width tracks the source text's length (its vertical height
        # is a proxy for char count), capped at 60% page. A fixed 60%-page strip
        # blew up SHORT vertical labels — a 2-char 大吉 (Great fortune) typeset
        # across a page-wide strip became an enormous font.
        nw = max(w, min(int(h * 1.5), int(page_w * 0.6)))
    else:
        nw = w
    nx = max(0, min(x, page_w - nw))  # keep it on-page
    nh = max(int(h * 1.5), int(page_h * 0.06))
    ny = max(0, y + h // 2 - nh // 2)  # center the strip on the source text
    return (nx, ny, nw, nh)


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
    lang: str = "auto",
    lookahead: np.ndarray | None = None,
    carryover: list | None = None,
) -> tuple[Image.Image, list[TextBlock], int, int, list]:
    """Run the full pipeline on one page.

    Returns (result PIL image, blocks with translations, prompt_tokens,
    completion_tokens) — the token counts let the caller price the page.

    `dry_run=True` runs detect + OCR but skips the LLM translation (no API call,
    no cost) — the page is returned unchanged with blocks carrying empty
    translations, so a dry-run job records what was *found* without spending.

    `gpu_worker_url`, when set, offloads detect+OCR and inpaint to the remote
    GPU worker; failures fall back to the local CPU models.

    `lang` is the source language: "ja" (manga-ocr / GPU worker), "ko" or "zh"
    (PaddleOCR PP-OCRv5/v6, local CPU), or "auto" to detect it from the page.

    `progress_cb(stage)`, when provided, is called with one of ``detect``,
    ``ocr``, ``translate``, ``inpaint``, ``typeset`` as the page advances through
    the pipeline — the worker uses it to drive a finer-grained progress bar than
    whole-page granularity.
    """
    emit = progress_cb or (lambda _stage: None)

    image = Image.fromarray(load_image(image_path))
    image_np = np.asarray(image)
    page_w, page_h = image.width, image.height

    # Blank dividers are left byte-for-byte unchanged — no dialogue to translate.
    if _is_blank(image_np):
        return image, [], 0, 0, []

    if lang == "auto":
        lang = detect_language(image)
    if lang not in ("ja", "ko", "zh"):
        lang = "ja"

    # JP colour pages (covers/splashes) carry no dialogue and the B/W pipeline
    # mangles colour art — skip them. Korean webtoons & Chinese manhua are
    # coloured BY DESIGN and carry dialogue, so only the JP path skips colour.
    if lang == "ja" and _is_color(image):
        return image, [], 0, 0, []

    # ---- stitch lookahead (boundary-spanning bubbles) ------------------------
    # Vertical-scroll webtoons cut a speech bubble at the page boundary: its top
    # half lands here and bottom half on the next page, so OCR reads half-glyphs
    # → garbage translation. Stitching the next page's top strip onto the bottom
    # of this page lets detect/OCR see the WHOLE bubble. The below-boundary half
    # is carried to the next page (see the typeset/paste section) so the English
    # stays continuous when pages are re-stacked into the vertical scroll.
    if lookahead is not None and lookahead.shape[1] == page_w:
        image_np = np.vstack([image_np, lookahead])
        image = Image.fromarray(image_np)

    # ---- detect + OCR --------------------------------------------------------
    bubbles = None
    blocks = None
    if lang in ("ko", "zh"):
        # Multilingual OCR (PaddleOCR PP-OCRv5/v6) — webtoon/manhua text,
        # including VERTICAL lines (PaddleOCR's textline-orientation classifier
        # rotates vertical text to horizontal before recognition). It returns no
        # bubble regions, so the typesetter places each block at its own box
        # (webtoon speech boxes are rectangles, not drawn bubbles).
        emit("detect")
        boxes = None
        if gpu_worker_url:
            try:
                # Offload to the GPU worker (same PaddleOCR, run off the app's
                # memory-constrained CPU); fall back to local on any failure.
                boxes = remote_ocr_multilingual(image, gpu_worker_url, lang)
                drop_all_pipelines()  # worker does the OCR now; free local models
            except Exception:
                boxes = None
        if boxes is None:
            boxes = read_boxes_text(image, lang)
        # Merge horizontally-adjacent word fragments (PaddleOCR splits a spaced
        # Korean line into one box per word — '좋지 않아?' -> '좋지' + '않아?')
        # into one box per line, then merge vertically-stacked lines into one
        # block so the whole bubble is translated + lettered as a unit.
        if lang == "ko":
            boxes = _merge_horizontal_words(boxes)
        boxes = _merge_stacked_lines(boxes)
        blocks = [
            TextBlock(bbox=(x, y, w, h), text=text, confidence=conf,
                      orientation=_orientation(w, h), angle=angle)
            for (x, y, w, h), text, conf, angle in boxes
            if text and _has_japanese(text) and not is_noise_box(w, h, conf)
            and not _drop_low_confidence(conf)
        ]
        # Speech-bubble boundaries from the SAME RT-DETR detector the ja path
        # uses — it finds the bubble regions automatically (language-agnostic),
        # so English lettering sizes + fits into the real bubble instead of
        # relying on the fragile colour flood-fill. Reuses the worker's
        # /detect-ocr (its manga-ocr OCR output is discarded — only `bubble` is
        # wanted here). Falls back to [] so the typesetter uses find_speech_box.
        bubbles = None
        if gpu_worker_url:
            try:
                bubbles = remote_detect_ocr(image, gpu_worker_url).get("bubble") or []
            except Exception:
                bubbles = []
    elif gpu_worker_url:
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
    # which returns blocks without this filter. Also dedup nested/overlapping
    # blocks (the worker returns the same region at several granularities) and
    # split bulleted stat columns into per-line blocks.
    blocks = _dedup_blocks(
        _drop_corner_watermarks(
            _drop_non_japanese(blocks), page_w, page_h
        )
    )
    if lang == "ja":
        # Large stylized titles/logos (series title, section headers) are
        # mis-OCR'd by manga-ocr, so leave them untouched — but only for
        # Japanese. Korean/Chinese webtoon dialogue is horizontal and always
        # translated; the height heuristic would wrongly drop a multi-line
        # speech bubble as a "title" (e.g. a 5-line bubble > 15% of the page).
        blocks = _drop_titles(blocks, page_h)
    blocks = _split_bullet_lines(blocks)

    # ---- boundary filtering (lookahead / carryover) --------------------------
    # Drop blocks that belong to the next page (entirely in the lookahead strip)
    # or that were already resolved by the previous page (their bottom half was
    # drawn there and carried over here as a paste patch).
    if lookahead is not None or carryover:
        carryover_boxes = [tuple(c[1]) for c in (carryover or [])]
        blocks = [
            b for b in blocks
            if not (lookahead is not None and b.bbox[1] >= page_h)
            and not _overlaps_any(b.bbox, carryover_boxes)
        ]

    # ---- translate (cloud LLM) ----------------------------------------------
    if dry_run:
        pt = ct = 0
        for b in blocks:
            b.translation = ""
    else:
        emit("translate")
        # Translate horizontal stat text only on pages that also carry vertical
        # content (stat/character pages) or a chapter heading (table-of-contents
        # / chapter-title pages) — a pure-horizontal cover/credit page with only
        # titles + credits is left as-is. Korean/Chinese webtoons & manhua are
        # the exception: their dialogue is horizontal, so always translate it.
        has_vertical = any(b.orientation == "vertical" for b in blocks)
        has_chapter = _has_chapter_heading(blocks)
        blocks, pt, ct = translate_page(
            blocks, model, api_key, base_url,
            translate_horizontal=has_vertical or has_chapter or (lang in ("ko", "zh")),
            source_lang=lang,
        )

    # ---- resolve typeset targets + erase boxes -------------------------------
    targets: list[tuple[TextBlock, tuple]] = []
    erase: list[tuple] = []
    if lang in ("ko", "zh"):
        # Webtoon/manhua: the OCR box is the TIGHT text region, not the speech
        # box. Resolve the enclosing speech box in order of trust:
        #   1) RT-DETR `bubble` (the ja path's detector) — finds the boundary
        #      automatically, inset to its inscribed rectangle so oval/spiked
        #      bubbles don't spill.
        #   2) colour flood-fill (`find_speech_box`) — for bubbles the detector
        #      missed.
        #   3) caption strip — free-floating text with no enclosing box at all.
        for b in blocks:
            if b.orientation == "furigana":
                erase.append(b.bbox)
                continue
            if not b.translation:
                continue
            raw = find_parent_bubble(bubbles, b.bbox) if bubbles else None
            if raw is not None:
                region = _inset_box(raw)
            else:
                sb = find_speech_box(image_np, b.bbox)
                if sb:
                    raw = sb
                    region = _inset_box(sb)
                else:
                    # Free-floating text on artwork (vertical caption or horizontal
                    # footnote) with no enclosing box: English is always horizontal,
                    # so letter it across a generous strip instead of fitting it to
                    # the source text's (tall-narrow or short-wide) box shape.
                    raw = None
                    region = _caption_region(b.bbox, page_w, page_h)
            # Tilt the English lettering to match a slanted speech box. The
            # angle comes from the box's fill shape (region_angle), not the OCR
            # quad — the GPU worker returns only axis-aligned boxes. Vertical
            # source text is re-lettered horizontally, so it is never rotated.
            b.angle = (
                region_angle(image_np, raw)
                if raw is not None and b.orientation != "vertical"
                else 0.0
            )
            targets.append((b, region))
            erase.append(b.bbox)
    elif bubbles is not None:
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
                    region = _inset_box(region)
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
    # Erase the Korean in carryover regions even if detection missed them, so a
    # pasted English patch never sits on un-erased source text.
    for _patch, cbox in (carryover or []):
        erase.append(tuple(cbox))
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

    # ---- paste carryover patches (previous page's bubble bottom halves) ------
    if carryover:
        result = Image.fromarray(_paste_carryover(np.asarray(result), carryover))

    # ---- carry boundary bubbles' bottom halves to the NEXT page --------------
    # A bubble that straddles the cut is rendered once (into its full region,
    # which extends into the lookahead strip). Cropping keeps the top half here
    # and the extracted patch hands the bottom half to the next page, so the
    # split bubble's English lines up when pages are re-stacked vertically.
    carryover_out: list = []
    if lookahead is not None:
        carryover_out = _extract_carryover(np.asarray(result), targets, page_h)

    if result.height > page_h:
        result = result.crop((0, 0, result.width, page_h))
    return result, blocks, pt, ct, carryover_out
