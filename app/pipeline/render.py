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

import difflib
import re

import numpy as np
from PIL import Image

from .bubble import find_container, find_speech_box, is_free_floating, region_angle, find_balloon_gap_tolerant
from .detector import detect_containers, find_parent_bubble
from .ingest import load_image
from .inpaint import inpaint_text, stroke_boxes
from . import fitlog
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


# Punctuation/marks carry no evidence of *which* utterance this is: manga-ocr's
# screentone hallucinations and real lines differ only in their kana/kanji.
_DUP_PUNCT = re.compile(r"[\s、。．，,\.！？!?…‥ー〜～「」『』（）()\[\]【】・:;'\"-]")


def _norm_utterance(text: str) -> str:
    """The string with all punctuation and spaces removed, for comparing two
    detections: 'そういえば、' and 'そういえば．．．' are the SAME utterance."""
    return _DUP_PUNCT.sub("", text or "")


def _same_utterance(a: str, b: str, ratio: float = 0.75) -> bool:
    """True if two OCR results read as the same line (identical, or near-identical
    once punctuation is stripped). Length similarity is required first so that a
    whole region and its sub-lines — the worker returns both — never count as the
    same utterance, however much they share."""
    na, nb = _norm_utterance(a), _norm_utterance(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if min(len(na), len(nb)) / max(len(na), len(nb)) < 0.6:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= ratio


def _text_truncates(long_text: str, short_text: str) -> bool:
    """True if `short_text` is a truncated reading of `long_text`.

    Two detections over one region where the shorter is a prefix/slice of the longer
    is a duplicated read, not two lines (job-4 page 60: 'あってますけど！！' inside
    'あってますけど！！なんでそれはわかるかなあ！'). The same shape appears when the
    worker returns a region and its individual lines — dropping the contained reading
    is what ``_dedup_blocks`` already does for the nested case."""
    a, b = _norm_utterance(long_text), _norm_utterance(short_text)
    return len(b) >= 2 and len(b) < len(a) and b in a


def _duplicate_clusters(blocks: list[TextBlock]) -> list[list[int]]:
    """Groups of blocks the detector emitted more than once for one region.

    Membership needs BOTH signals: the boxes must overlap substantially, and the two
    readings must be related (same utterance, or one a truncated read of the other).
    `_dedup_blocks` alone misses these because its thresholds (IoU > 0.5 /
    containment > 0.85) assume nested output, while the screentone failure is a run of
    boxes jittered a few px — they cover 25-70% of the smaller box (job-4 page 118:
    six boxes across 80px, chained, so union-find is used). The text requirement keeps
    genuinely distinct adjacent columns out, and the 0.25 containment floor keeps
    blocks that merely clip a corner apart (job-4 page 58 has two drawn `ふる` SFX
    whose boxes touch over 12x1px — those are two different glyphs, not a duplicate).
    """
    n = len(blocks)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def related(a: TextBlock, b: TextBlock) -> bool:
        return (_same_utterance(a.text, b.text)
                or _text_truncates(a.text, b.text)
                or _text_truncates(b.text, a.text))

    for i in range(n):
        for j in range(i + 1, n):
            a, b = blocks[i], blocks[j]
            if _box_containment(a.bbox, b.bbox) <= 0.25:
                continue
            if not related(a, b):
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return [m for m in clusters.values() if len(m) > 1]


def _collapse_duplicate_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Letter a repeated detection once — and drop a degenerate repeat entirely.

    Left alone, every duplicate is translated and lettered into the same spot, so a
    page carries several overlapping copies of one English line (job-4 page 118: six
    boxes over 80x240px of pure screentone → five overlapping "SPEAKING OF WHICH,",
    drawn onto artwork that holds no text at all). Two rules:
      * a group of 2+ blocks reading as the same utterance keeps its most complete
        text — the longest reading wins, so a truncated duplicate is dropped (job-4
        page 60: 'あってますけど！！' inside 'あってますけど！！なんでそれはわかるかなあ！');
      * if a cluster contains 3+ blocks with an IDENTICAL reading the whole cluster
        goes: a genuine line is never detected three times in one place, so this is
        a detector/OCR repetition artefact, and keeping one copy would letter English
        onto artwork that holds no text.
    Deliberately geometric+textual only. A pixel-texture test for "is this really
    text?" was tried and measured — it does NOT separate halftone from real
    thin-stroke glyphs (a real 31x76 'うん' scores the same as screentone), so a
    single phantom that appears ONCE is left alone rather than risk a real line.
    """
    if len(blocks) <= 1:
        return blocks
    drop: set[int] = set()
    for members in _duplicate_clusters(blocks):
        groups: list[list[int]] = []
        for i in members:
            for g in groups:
                if _same_utterance(blocks[i].text, blocks[g[0]].text):
                    g.append(i)
                    break
            else:
                groups.append([i])
        if any(len(g) >= 3 and len({_norm_utterance(blocks[i].text) for i in g}) == 1
               for g in groups):
            # Degenerate cluster: 3+ identical readings for one region is a detector
            # artefact over art, so the whole run goes — along with any OTHER short
            # block overlapping the run's footprint (job-4 page 118: the 6th box,
            # reading そういうことで、, sits across the end of the run).
            drop.update(members)
            ux0 = min(blocks[i].bbox[0] for i in members)
            uy0 = min(blocks[i].bbox[1] for i in members)
            ux1 = max(blocks[i].bbox[0] + blocks[i].bbox[2] for i in members)
            uy1 = max(blocks[i].bbox[1] + blocks[i].bbox[3] for i in members)
            union = (ux0, uy0, ux1 - ux0, uy1 - uy0)
            for i, b in enumerate(blocks):
                if i in drop or _glyph_count(b.text) > 8:
                    continue
                if _box_containment(b.bbox, union) > 0.25:
                    drop.add(i)
            continue
        survivors: list[int] = []
        for g in groups:
            if len(g) <= 1:
                survivors.extend(g)
                continue
            best = max(g, key=lambda i: (_glyph_count(blocks[i].text),
                                         blocks[i].bbox[2] * blocks[i].bbox[3]))
            survivors.append(best)
            drop.update(i for i in g if i != best)
        # A survivor that is only a truncated read of another survivor is dropped:
        # one reading per region, and the most complete one wins.
        for i in survivors:
            if any(j != i and _text_truncates(blocks[j].text, blocks[i].text)
                   for j in survivors):
                drop.add(i)
    if not drop:
        return blocks
    return [b for i, b in enumerate(blocks) if i not in drop]


def _drop_titles(blocks: list[TextBlock], page_h: int) -> list[TextBlock]:
    """Skip large horizontal title/header text (series title, section headers,
    logos).

    manga-ocr misreads decorative title lettering (e.g. 月が導く異世界道中 OCR'd
    as 日道異世界中の建築), so "translating" it produces nonsense. Titles/headers
    are proper nouns / logos — leave them as-is. A block that is BOTH taller than
    ~15% of the page AND wider than it is tall is a large-font horizontal
    title/header; a tall-narrow bio paragraph or vertical name banner is kept.

    Vertical title art is the same problem on its side: a big brush-drawn
    vertical title (job-3 p187's 月夜に提灯, 379x437) is read as vertical
    dialogue, OCR'd WRONG (提灯 -> 提出, "lantern" -> "submitted"), erased and
    lettered with the mistranslation. A real vertical dialogue column packs many
    glyphs into its area; a brush title has a few large strokes. Measured over
    every job-3 vertical block >= 40000px2: the title is the ONLY one under
    0.8 glyphs per 10k px2 (0.3); the next-lowest real dialogue is 1.08. So a
    large vertical block with very few glyphs for its size is title art — leave
    it as-is rather than erase + mistranslate it.
    """
    if page_h <= 0:
        return blocks
    out: list[TextBlock] = []
    for b in blocks:
        x, y, w, h = b.bbox
        if h > 0.15 * page_h and w > h:
            continue  # large horizontal title/header
        area = w * h
        if (b.orientation == "vertical" and area >= 100000
                and len((b.text or "").strip()) / area * 10000 < 0.8):
            continue  # large vertical brush title — few glyphs for its size
        out.append(b)
    return out


def _bubble_without_text(box: tuple, blocks: list[TextBlock]) -> bool:
    """True if no block's centre falls inside the bubble `box`."""
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    for b in blocks:
        bcx, bcy = b.bbox[0] + b.bbox[2] / 2, b.bbox[1] + b.bbox[3] / 2
        if box[0] <= bcx <= box[0] + box[2] and box[1] <= bcy <= box[1] + box[3]:
            return False
    return True


def _bubble_covered(box: tuple, blocks: list[TextBlock]) -> bool:
    """True if a block already occupies the bubble — by OVERLAP, not just centre.

    A detection box can cover only part of a balloon (job-4 page 92: the bubble box
    was the top line of a 3-line caption, so the block's centre sat below it). Centre
    containment then reports "no text here" and the fallback letters that balloon a
    second time — English over English. Any block that is mostly inside the bubble
    (or overlaps it substantially) counts as covering it.
    """
    for b in blocks:
        bx, by, bw, bh = b.bbox
        if bw <= 0 or bh <= 0:
            continue
        ix = max(0, min(box[0] + box[2], bx + bw) - max(box[0], bx))
        iy = max(0, min(box[1] + box[3], by + bh) - max(box[1], by))
        inter = ix * iy
        if inter <= 0:
            continue
        if inter / (bw * bh) >= 0.25:
            return True                      # block mostly inside the bubble
        union = bw * bh + box[2] * box[3] - inter
        if union and inter / union >= 0.15:  # or substantial mutual overlap
            return True
    return False


def _partition_shared_bubble(targets: list) -> None:
    """Reconcile overlapping balloon regions so two blocks' text can't collide.

    Two stacked speech balloons that touch (or one figure-eight the detector
    returns as two overlapping boxes) give each block a region whose bbox
    OVERLAPS the neighbour's. The typesetter centres each block into its own
    region, and the two English texts print through each other in the overlap
    (job-3 p45 "ATTEMPT" through "IN MIND-", p72 "PRACTICAL" through
    "ADVERTISE", p12 "AT BOTH" through "INCHES"). The blocks are vertically
    stacked, so split the overlapping pair at the vertical midpoint of their
    overlap: the upper block's region stops there, the lower block's starts
    there. Each keeps its full width and its own centre; only the shared edge
    moves. Regions that don't overlap another are untouched.

    `targets` is the list of (block, region) pairs built by the caller; regions
    are updated in place.
    """
    def _ov(a, b):
        ox = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
        oy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
        return ox * oy

    n = len(targets)
    for i in range(n):
        bi, ri = targets[i]
        if ri is None:
            continue
        ri = tuple(int(v) for v in ri)
        for j in range(i + 1, n):
            bj, rj = targets[j]
            if rj is None:
                continue
            rj = tuple(int(v) for v in rj)
            if _ov(ri, rj) <= 0:
                continue
            # vertical overlap of the two REGIONS: split it between them.
            top, bot = (ri, rj) if ri[1] <= rj[1] else (rj, ri)
            top_i = i if ri[1] <= rj[1] else j
            oy_top = max(top[1], bot[1])           # where the lower region starts
            oy_bot = min(top[1] + top[3], bot[1] + bot[3])  # where the upper ends
            if oy_bot <= oy_top:
                continue
            vov = oy_bot - oy_top
            smaller_h = min(top[3], bot[3])
            # Only the genuine stacked/touching case: the regions share a large
            # vertical band (one balloon's bottom deep inside the other's top).
            # Balloons that merely nestle (a few px of rounded corner) are left
            # alone — their centred text does not reach the overlap.
            if smaller_h <= 0 or vov < 0.4 * smaller_h:
                continue
            mid = (oy_top + oy_bot) // 2
            # upper region: keep its top, stop at mid. lower: start at mid.
            new_top = (top[0], top[1], top[2], max(1, mid - top[1]))
            new_bot = (bot[0], mid, bot[2], max(1, bot[1] + bot[3] - mid))
            if top_i == i:
                targets[i] = (bi, new_top)
                targets[j] = (bj, new_bot)
            else:
                targets[i] = (bi, new_bot)
                targets[j] = (bj, new_top)
            ri, rj = (new_top, new_bot) if top_i == i else (new_bot, new_top)


# A balloon holding a lone glyph (「真」 with its furigana まこと riding beside it) is
# invisible to the text-region detector: it is not a text LINE, so neither
# `text_bubble` nor `text_free` fires and no OCR is ever attempted. RT-DETR still finds
# the balloon, so "bubble with no text block inside" is the signature of this miss
# class (job-5 page 3, job-4 page 7). OCR'ing the balloon interior directly recovers
# it — but only for balloons that really hold a glyph, because OCR on artwork
# hallucinates plausible Japanese out of line-work and screentone.
_MIN_BUBBLE_INK = 0.004   # fraction of dark pixels — an empty balloon has none
# ...and an UPPER bound: a drawn glyph that fills most of the balloon is ART (job-4
# page 28's starburst kanji measures 23% ink; real dialogue glyphs measure 1-11%,
# the reported 真 is 6%). Such a balloon is left as-is rather than risk lettering a
# misread word over the drawing.
_MAX_BUBBLE_INK = 0.18
_KANA = re.compile(r"[\u3041-\u309f\u30a0-\u30ff]")
_KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _ink_is_glyph_like(gray: "np.ndarray", box: tuple) -> bool:
    """True if the ink inside `box` looks like a text glyph, not artwork.

    The discriminator that separates a glyph from a drawing (measured on the real
    cases): **a glyph does not touch the box border**. A character sits inside the
    balloon with white around it, while artwork — a portrait inside a balloon, a
    starburst's drawn kanji, a caption box cropped mid-line — runs to the edge of any
    inset you measure on. Measured: 真 ink 6% touches_border=False; balloon artwork
    25%/40% and the cropped caption 10% all touch it.
    """
    x, y, w, h = [int(v) for v in box]
    if not hasattr(gray, "shape"):
        gray = np.asarray(gray)
    H, W = gray.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    patch = gray[y0:y1, x0:x1]
    ink = patch < 128
    if not ink.any():
        return False
    return not bool(ink[0, :].any() or ink[-1, :].any()
                    or ink[:, 0].any() or ink[:, -1].any())


def _recoverable_text(text: str) -> bool:
    """Guard the OCR result: real dialogue, not a fragment or an SFX mark.

    A lone KANA in a balloon is almost always one glyph of a longer line that the
    detector clipped (の, ぅ, ぁ) — lettering "of" into that balloon is worse than
    leaving it. A lone KANJI is a word or a name (真, 私, 若) and is the case this
    exists for, so single kanji pass. Punctuation-only results (～～〜〜！？) are SFX
    marks and stay as they are.
    """
    t = (text or "").strip()
    if not t:
        return False
    if len(t) >= 2:
        return bool(_KANA.search(t) or _KANJI.search(t))
    return bool(_KANJI.search(t))


_PUNCT_ONLY = re.compile(r"[\s、。．，,\.！？!?…‥ー〜～「」『』（）()\[\]【】]")


def _glyph_count(text: str) -> int:
    """Characters that actually have to be drawn (punctuation is nearly free)."""
    return len(_PUNCT_ONLY.sub("", text or ""))


def _ink_matches_length(ink_frac: float, text: str, ink_frac_per_glyph: float = 0.05) -> bool:
    """Is the balloon's ink consistent with the number of characters OCR returned?

    manga-ocr misreads a single stylized glyph as a short word: job-4 page 88's 響 (a
    brush-drawn name) came back as それぞれ、, which would have lettered "EACH OF YOU,"
    into a name balloon. A line of kana needs far more ink than one glyph, so when the
    interior holds less than `ink_frac_per_glyph` ink per character at most two are
    plausible. Calibrated on the real cases: one glyph measures 1.4-10.5% of the inset
    (真 6.2%, 響 6.7%), so four kana would need ≈20% while page 88's balloon holds
    6.7% — the misread is rejected. Erring high only leaves a balloon Japanese; erring
    low letters a wrong word into it.
    """
    n = _glyph_count(text)
    if n <= 2:
        return True
    return ink_frac >= ink_frac_per_glyph * n


def _ocr_bubbles_without_text(image_np: np.ndarray, bubbles, blocks: list[TextBlock],
                              ocr_fn=None, min_ink: float = _MIN_BUBBLE_INK,
                              max_ink: float = _MAX_BUBBLE_INK) -> list[TextBlock]:
    """OCR balloons the text detector missed, so no speech bubble stays Japanese.

    Runs only for a balloon that no detected block occupies (`_bubble_covered`) and
    whose interior holds glyph-like ink (`_ink_is_glyph_like`) in a plausible amount
    (between `min_ink` and `max_ink` — measured on the deeper inset so the balloon's
    own outline is excluded) — artwork, empty balloons and drawn oversized glyphs
    cost no OCR call. The result is kept when it reads as real dialogue
    (`_recoverable_text` + `_has_japanese`).
    """
    if not bubbles:
        return []
    gray = image_np if image_np.ndim == 2 else np.asarray(
        Image.fromarray(image_np).convert("L"))
    H, W = gray.shape[:2]
    out: list[TextBlock] = []
    for bb in sorted(bubbles, key=lambda b: -(b[2] * b[3])):
        bb = tuple(int(v) for v in bb)
        if _bubble_covered(bb, blocks) or _bubble_covered(bb, out):
            continue
        ix, iy, iw, ih = _inset_box(bb, 0.14, 0.12)
        x0, y0 = max(0, ix), max(0, iy)
        x1, y1 = min(W, ix + iw), min(H, iy + ih)
        if x1 - x0 < 12 or y1 - y0 < 12:
            continue
        kx, ky, kw, kh = _inset_box(bb, 0.22, 0.20)
        # deeper inset: an empty balloon and a balloon full of artwork both measure
        # zero ink here, and the outline cannot be mistaken for text
        patch = gray[max(0, ky):ky + kh, max(0, kx):kx + kw]
        if patch.size == 0:
            continue
        ink_frac = float((patch < 128).mean())
        if ink_frac < min_ink or ink_frac > max_ink:
            continue
        if not _ink_is_glyph_like(gray, (kx, ky, kw, kh)):
            continue  # artwork runs to the edge of the inset — not a glyph
        if ocr_fn is None:
            from .ocr import ocr_crop  # local: only pulls in torch/manga-ocr if needed
            ocr_fn = ocr_crop
        text, _conf = ocr_fn(image_np, (ix, iy, iw, ih))
        text = (text or "").strip()
        if not _has_japanese(text) or not _recoverable_text(text):
            continue
        if not _ink_matches_length(ink_frac, text):
            continue  # too little ink for that many glyphs — a misread, not a line
        out.append(TextBlock(bbox=(ix, iy, iw, ih), text=text,
                             confidence=None, orientation="vertical"))
    return out


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


# How much of the next page's top edge to sample when reading a bubble that
# straddles the page cut (webtoon slicers cut at arbitrary heights).
LOOKAHEAD_BAND = 500


def _ocr_ko_zh_native(page_image, page_np, lookahead, worker_url, lang: str,
                      band: int = LOOKAHEAD_BAND, ocr_fn=None) -> list:
    """OCR a Korean/Chinese page at NATIVE scale, boundary bubble included.

    PaddleOCR downsizes whatever image it is handed (long side to ~960 px), so
    OCRing a page with the lookahead strip stitched onto it shrinks the effective
    glyph height and the recognizer degrades into garbage syllables. Measured on
    job-2 page 77: the same line reads '현성아!' at native scale (conf 0.999) but
    '야워을' with a 500 px strip attached (conf 0.609); the page's clean 7 boxes
    came back as 6 nonsense ones, which the pipeline then translated, erased and
    lettered — and because the garbage boxes only partly covered the real text,
    the Korean stayed on the page with English drawn over it.

    Fix: OCR the page alone, then OCR only the boundary BAND (the page's last
    `band` rows joined to the next page's first `band` rows) as its own image —
    2×band tall, so it too stays near native scale — and map the band's boxes
    back into page coordinates. Boxes living entirely inside the band are dropped
    from the page pass in favour of the band's reading; lines that merely straddle
    the band's top edge keep the page's fuller reading (the existing dedup drops
    the band's partial copy). A bubble spanning the cut is therefore read whole,
    which is what the strip was for.

    `ocr_fn(image, lang)` is the recognizer used for both passes (defaults to the
    GPU worker; the local PP-OCR fallback passes `read_boxes_text`) — both paths
    were handed the stitched image before this and both degraded the same way.

    The band is rescaled so its LONG side matches the page's long side. PaddleOCR's
    recognition is size-sensitive, not just aspect-sensitive: the raw 690x1000
    join is read as garbage by BOTH backends ('이 위아래로' -> '래러이ㅎ',
    conf 0.999 -> 0.670 — measured; the same pixels inside the 690x1600 page read
    cleanly). Matching the page's own resolution puts the glyphs back in the size
    range the recognizer handles, and is verified to keep the straddling-bubble
    reading that the band exists for (page 23, whose bubble is invisible to the
    page pass, still reads correctly).
    """
    call = ocr_fn or (lambda img, lg: remote_ocr_multilingual(img, worker_url, lg))
    boxes = call(page_image, lang)
    if lookahead is None or lookahead.shape[1] != page_image.width:
        return boxes
    page_h = page_image.height
    band = min(band, page_h, lookahead.shape[0])
    if band <= 0:
        return boxes
    joined = np.vstack([page_np[page_h - band:], lookahead[:band]])
    sc = page_h / joined.shape[0]
    if abs(sc - 1.0) > 0.01:
        joined_img = Image.fromarray(joined).resize(
            (max(1, int(round(joined.shape[1] * sc))), page_h), Image.LANCZOS)
        inv = 1.0 / sc
    else:
        joined_img, inv = Image.fromarray(joined), 1.0
    off = page_h - band
    # Map the band's boxes back to page coordinates, and keep them INTEGERS:
    # the rescale makes them fractional, and downstream code slices arrays with
    # these boxes (erase/inpaint/carryover), which raises "'float' object cannot
    # be interpreted as an integer" — 10 of job 2's 129 pages died that way
    # (v0.27.4). Round the corners, then derive w/h from them so the box does not
    # drift.
    def _back(bx, by, bw, bh):
        x0 = int(round(bx * inv))
        y0 = int(round(by * inv)) + off
        x1 = int(round((bx + bw) * inv))
        y1 = int(round((by + bh) * inv)) + off
        return (x0, y0, x1 - x0, y1 - y0)

    band_boxes = [
        (_back(x, y, w, h), text, conf, angle)
        for (x, y, w, h), text, conf, angle in call(joined_img, lang)
    ]
    # v0.27.15: the page's own NATIVE read is authoritative for text that sits
    # fully inside the page. Pre-dropping every page box that started inside the
    # band let a worse band reading win: job-2 page 29's page pass read
    # '발단은'/'5년전' at conf 1.000 at native scale, the rescaled band read '긍'
    # (0.952) over the same pixels, the band version replaced them, and the
    # pipeline then erased one line's box and lettered "Mm." over the Korean that
    # survived. `_band_handover` already reconciles the passes correctly — a band
    # box that CROSSES the cut supersedes this page's partial view, and a band box
    # that duplicates a page box is dropped in favour of the native read.
    kept = _band_handover(boxes, band_boxes, page_h)
    if len(kept) != len(boxes) or band_boxes:
        print(f"[ocr] native={len(boxes)} -> {len(kept)} after band handover, "
              f"band={len(band_boxes)} (offset {off})")
    return kept


def _band_handover(page_boxes: list, band_boxes: list, page_h: int) -> list:
    """Reconcile the page pass and the boundary-band pass into one box list.

    The two passes overlap by design (the band is the page's last `band` rows
    joined to the next page's first `band` rows), so the same pixels can be read
    twice. Naively concatenating them double-counts a line: page 77's boundary
    line came back as '살아!' from BOTH passes and `_merge_horizontal_words`
    joined the pair into '살아! 살아!' — which the LLM then lettered as
    "Live here! Live here!". Two rules, in order:

    1. A band box that CROSSES the page cut is the authoritative reading of a
       boundary bubble (the page pass only saw its top). Drop this page's
       partial view of it — otherwise the partial version is lettered as well.
    2. A band box that stays inside the page and duplicates a surviving page box
       is redundant — drop it in favour of the page's own (fuller, native) read.
    """
    if not band_boxes:
        return page_boxes
    crossing = [b for b in band_boxes if b[0][1] + b[0][3] > page_h]
    out_page = [
        b for b in page_boxes
        if not any(_box_containment(b[0], c[0]) > 0.6 for c in crossing)
    ]
    out_band = [
        b for b in band_boxes
        if not any(_box_containment(b[0], p[0]) > 0.6 for p in out_page)
    ]
    return sorted(out_page + out_band, key=lambda b: (b[0][1], b[0][0]))


def _is_drawn_sfx(block) -> bool:
    """True for a drawn sound effect / short free-standing art text (ko/zh).

    These have no speech bubble and only a few characters, and their OCR box is
    TIGHT around the lettering (PaddleOCR returns the text region, not a bubble),
    so the box is literally the footprint the sound effect occupies on the art.

    Lettering them like a caption was the bug: the font cap came from the PAGE
    width (`_draw_box` uses ~width/32, i.e. 21px on a 690px webtoon page), so
    job-2 page 85's page-wide `조~으~옹..` — 421px tall — was erased and replaced
    by a 21px "Quiet." floating in the inpainted smear. Filling the tight box
    instead puts English where the sound effect was, at a comparable size.
    """
    t = (block.text or "").strip()
    if not t or len(t) > 8:
        return False
    x, y, w, h = block.bbox
    if h < 28 or w * h < 60000:
        return False  # a small label/stamp, not a drawn glyph block
    if block.orientation == "horizontal":
        return True
    # A drawn SFX is often STACKED down the page (three big glyphs across a page
    # cut), so the classifier labels it `vertical` and it used to fall through to
    # `_caption_region` — where the font cap comes from the PAGE width (690/32 =
    # 21px on a webtoon) and erased art came back as a 21px word. Job-2 page 40's
    # `더다다` (476x792) lettered at 21px while its twin on page 39 (`튼다다`,
    # 502x735, labelled horizontal) got 109px; the blocks differ only in that
    # label. A wide box can hold horizontal English at a size that fills it, so
    # letter it into its own footprint (measured to flip exactly this one block
    # across jobs 1-3). A tall-narrow column (w < 0.55h) still keeps
    # `_caption_region`'s widening — English cannot fill a vertical column.
    return w >= 0.55 * h


_STUB_ART_MIN_AREA = 25000


def _ascii_share(text: str) -> float:
    """Share of the text's letters/digits that are plain ASCII (already-English)."""
    letters = [c for c in (text or "") if c.isalnum()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


def _drop_english_signage(blocks: list, bubbles: list) -> list:
    """Drop ko/zh blocks whose reading is mostly already-English type.

    A Korean shop sign carrying English ("OPEN EVENT / GRAND OPEN 4/25-12/25")
    comes back from OCR as a Latin-dominant string, is translated as if it were
    dialogue, and re-letters as nonsense over an erased sign — job-2 page 46's shop
    front was flattened into a white wash under "DPENON.ETER 40TH WEEK GRAND OPEN
    4/25-12/25 EYET". Leaving that block alone keeps the sign and its artwork; the
    block has no bubble, so it is signage rather than dialogue.
    """
    out = []
    for b in blocks:
        if _ascii_share(b.text or "") >= 0.6 and not (bubbles and find_parent_bubble(bubbles, b.bbox)):
            continue
        out.append(b)
    return out


def _drop_stub_art(blocks: list, bubbles: list) -> list:
    """Drop ko/zh blocks that are a one-glyph read of a large drawn-art region.

    PaddleOCR reads PART of a drawn sound effect but returns the box of the whole
    art: job-2 page 56's `치이즈` came back as a single character (`철`, confidence
    0.98) in a 384x457 box. Erasing that box destroys the artwork behind it and
    there is no real translation to letter — the block only existed because the OCR
    mis-read a picture. Leaving it as art beats a smeared patch plus junk English.
    Only blocks with no enclosing bubble are candidates (a real balloon can hold a
    single big glyph, e.g. `뭐?`).
    """
    out = []
    for b in blocks:
        x, y, w, h = b.bbox
        if (w * h >= _STUB_ART_MIN_AREA and _glyph_count(b.text or "") <= 1
                and not (bubbles and find_parent_bubble(bubbles, b.bbox))):
            continue
        out.append(b)
    return out


def _merge_blocks_per_bubble(blocks: list, bubbles: list) -> list:
    """Merge blocks that resolve to the SAME speech bubble into one block.

    A multi-line bubble can come back as several OCR blocks (one per line, or a
    line fragment that also swallowed a neighbouring SFX). Each resolves to the
    same parent bubble, and the typesetter centres BOTH into it — lettering
    English on top of English. Measured on job-2 page 34: 'CAN YOU HEAR ME?'
    drawn straight through 'L-SENBAE! SENBAE!' inside one hexagon bubble. Merging
    the blocks before translation fixes both the lettering collision and the
    translation (the bubble goes to the LLM as one unit instead of half a line
    at a time). Only blocks with a real parent bubble are grouped — free-floating
    text (stat columns, captions) has no bubble and is never merged here.
    """
    if len(blocks) <= 1 or not bubbles:
        return blocks
    grouped: dict = {}
    for b in blocks:
        parent = find_parent_bubble(bubbles, b.bbox)
        if parent is None:
            continue
        grouped.setdefault(tuple(parent), []).append(b)
    if not any(len(v) > 1 for v in grouped.values()):
        return blocks
    merged: list = []
    consumed: set = set()
    for b in blocks:
        if id(b) in consumed:
            continue
        parent = find_parent_bubble(bubbles, b.bbox)
        if parent is None:
            merged.append(b)
            continue
        key = tuple(parent)
        members = grouped.get(key) or [b]
        if len(members) == 1:
            merged.append(b)
            continue
        for m in members:
            consumed.add(id(m))
        members = sorted(members, key=lambda m: (m.bbox[1], m.bbox[0]))
        x0 = min(m.bbox[0] for m in members)
        y0 = min(m.bbox[1] for m in members)
        x1 = max(m.bbox[0] + m.bbox[2] for m in members)
        y1 = max(m.bbox[1] + m.bbox[3] for m in members)
        base = members[0]
        text = " ".join(m.text for m in members if m.text)
        print(f"[bubble] merged {len(members)} blocks in one bubble -> "
              f"{text[:60]!r}")
        merged.append(TextBlock(bbox=(x0, y0, x1 - x0, y1 - y0), text=text,
                                confidence=base.confidence,
                                orientation=base.orientation, angle=base.angle))
    merged.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
    return merged


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


def _stack_adjacent(a: tuple, b: tuple) -> bool:
    """True if box `a` sits directly above/below box `b` as the next line of the
    same speech box (horizontally overlapping column, small vertical gap).

    Used instead of comparing against a growing union bbox: see
    `_merge_stacked_lines` for the page-115 snowball this prevents.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x_overlap = min(ax + aw, bx + bw) - max(ax, bx)
    if x_overlap <= 0.3 * min(aw, bw):
        return False  # different column — never merge across a horizontal gap
    # Lines of ONE text block share a line height. Without this guard a drawn
    # sound effect sitting under a narration box merges into it: job-2 page 56's
    # `단지 맛이 끔찍하게 없었을 뿐.` (three 60-70px lines in a dark panel) swallowed
    # the 384x457 drawn `치이즈` below it, so the erase box covered both — erasing
    # the artwork behind them — and the translation/lettering landed between the
    # two (the narration panel came back EMPTY, with the English over the art).
    if max(ah, bh) > 3.0 * min(ah, bh):
        return False  # wildly different line heights — different elements
    gap = ay - (by + bh) if ay >= by else by - (ay + ah)
    return gap <= 0.6 * min(ah, bh)  # adjacent (or overlapping) lines


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
            # A nested/duplicate detection (the same text region OCR'd twice,
            # or a sub-region of an already-seen block) sits almost ENTIRELY
            # inside the block — near-full containment. A tilted multi-line
            # bubble's lines overlap only PARTIALLY: their axis-aligned boxes
            # overlap heavily because of the slant, but the text is distinct.
            # So reject only near-full containment (a real duplicate) and merge
            # partial overlap (an adjacent line of the same tilted bubble).
            if _box_containment((x, y, w, h), (bx, by, bw, bh)) > 0.9:
                continue  # nested/duplicate detection — not a distinct line
            # Test adjacency against each MEMBER box, never against the
            # accumulated union. The union's height grows with every merge, so a
            # union-derived `0.6 * min(h, bh)` threshold inflates and admits
            # ever-more-distant boxes — a snowball that swallowed a whole page
            # (job-2 page 115: four bubble lines plus three unrelated art
            # misreads chained into ONE 575x1417 block, and erasing that box
            # wiped the artwork behind them). Per-member gaps bound every merge
            # to geometry that genuinely adjoins the bubble.
            if not any(_stack_adjacent((x, y, w, h), m) for m in blk["members"]):
                continue  # not adjacent to any line already in this block
            nx = min(bx, x)
            ny = min(by, y)
            nx2 = max(bx + bw, x + w)
            ny2 = max(by + bh, y + h)
            blk["bbox"] = (nx, ny, nx2 - nx, ny2 - ny)
            blk["members"].append((x, y, w, h))
            blk["text"] += text
            blk["conf"] = max(blk["conf"], conf)
            blk["aw"] += angle * w  # width-weighted slant accumulator
            blk["ww"] += w
            placed = True
            break
        if not placed:
            blocks.append({"bbox": (x, y, w, h), "text": text, "conf": conf,
                           "aw": angle * w, "ww": w, "members": [(x, y, w, h)]})
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


def _intersects(a: tuple, b: tuple) -> bool:
    """True if two (x, y, w, h) boxes overlap at all.

    Stricter than `_overlaps_any` (which needs >50% containment or IoU): a
    carryover patch can clip the corner of a bubble this page owns — ~45%
    containment — and pasting it there still corrupts the lettering.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (min(ax + aw, bx + bw) > max(ax, bx)) and (min(ay + ah, by + bh) > max(ay, by))


def _paste_carryover(result_np: np.ndarray, carryover: list) -> np.ndarray:
    """Paste carryover patches (a previous page's bubble bottom halves) onto a
    rendered page array. Each entry is ``(patch, (x, y, w, h))`` in this page's
    coordinates.

    The caller must already have dropped patches that land on text THIS page
    owns (see the carryover filter in `render_translated_page`) — stamping a
    previous page's pixels over freshly lettered text corrupts it.
    """
    out = result_np.copy()
    for patch, (cx, cy, cw, ch) in carryover:
        out[cy:cy + ch, cx:cx + cw] = patch
    return out


def _drop_spurious_carryover(carryover: list, blocks: list) -> list:
    """Drop carryover patches that overlap text THIS page owns.

    A previous page's target region can bleed across the boundary and clip a
    bubble this page is about to letter. Pasting that patch would stamp old
    (source-bearing) pixels over the fresh lettering — the 2026-09-15 job-2
    page-41 defect, where page 40's patch carried '하고' back over page 41's
    clean "I'M RETIRING". Uses `_intersects` (any overlap), not `_overlaps_any`:
    that defect was only ~45% containment, which `_overlaps_any` ignores.
    """
    own = [b.bbox for b in blocks]
    return [c for c in (carryover or []) if not any(_intersects(tuple(c[1]), ob) for ob in own)]


def _plausible_balloon(cont, text_box: tuple, ref_box, page_w: int, page_h: int) -> bool:
    """Is `cont` — a white-flood box — a believable balloon for `text_box`?

    Used to decide whether to trust the flooded interior over the detector's own
    bubble box. The flood escapes any balloon whose outline has a gap and can
    swallow the whole panel, so require all of:

      * it **contains** the text column (a couple of px of slack for detector slop);
      * it is not page-sized (a leak);
      * it is not wildly larger than the box the detector named (`ref_box`) — the
        detector is often TIGHT (measured job-3 p12: 204x311 for a 307x413 balloon),
        but a 10x jump means the flood ran away.

    Job-3 p12 is the case this exists for: the detector's bubble box was ~half the
    balloon's area, so the lettering was sized to the small box — a small font,
    centred high, with the balloon's lower half left empty.
    """
    cx, cy, cw, ch = (int(v) for v in cont)
    tx, ty, tw, th = (int(v) for v in text_box)
    if cx > tx + 2 or cy > ty + 2 or cx + cw < tx + tw - 2 or cy + ch < ty + th - 2:
        return False
    if cw * ch > 0.55 * page_w * page_h:
        return False
    if ref_box is not None:
        rx, ry, rw, rh = (int(v) for v in ref_box)
        if cw * ch > 6.0 * max(1, rw * rh):
            return False
    return True


def _free_text_region(bbox: tuple, page_w: int, page_h: int, gray=None,
                      obstacles=None) -> tuple:
    """Typeset region for free-floating text with no enclosing speech box.

    English is HORIZONTAL, so a tall-narrow source box (Japanese tategaki
    narration, a vertical caption column over artwork) must not set the lettering
    width — fitting a long English line into a ~1-glyph-wide column produces
    microscopic text. Only tall-narrow text gets the wide strip; wide footnotes
    and small labels keep their own width (see `_caption_region`).
    """
    if bbox[3] > bbox[2] * 1.5:
        return _caption_region(bbox, page_w, page_h, gray=gray, obstacles=obstacles)
    return bbox


# A carryover patch only MEANS something if the previous page actually drew
# lettering into the boundary strip. A region that straddles the cut but whose
# English sat higher up yields a patch of bare inpainted background: pasting it
# achieves nothing, while the matching erase box on the next page destroys text
# there (2026-09-15 job-2 page 30 — a blank 196x83 patch erased the top of the
# page's own `그날` narration and left a smudge). Rendered lettering has contrast;
# bare inpaint does not (measured: blank 10.8 std vs real patches 49 / 72).
_MIN_PATCH_STD = 20.0


def _patch_is_blank(patch: np.ndarray) -> bool:
    """True if a carryover patch carries no rendered lettering (bare background)."""
    if patch is None or patch.size == 0:
        return True
    a = patch.astype(np.float32)
    if a.ndim == 3:
        a = a.mean(axis=2)
    return float(a.std()) < _MIN_PATCH_STD


def _extract_carryover(result_np: np.ndarray, targets: list, page_h: int) -> list:
    """Extract the below-boundary halves of bubbles that straddle `page_h`.

    Returns ``[(patch, (x, 0, w, h))]`` — each patch is the rendered bottom half
    of a boundary bubble, in the NEXT page's coordinates (y shifted to 0).
    Blank patches are dropped — see `_patch_is_blank`.
    """
    patches: list = []
    for _b, region in targets:
        if region is None:
            continue
        rx, ry, rw, rh = region
        if ry < page_h < ry + rh:
            bottom = min(ry + rh, result_np.shape[0])
            patch = result_np[page_h:bottom, rx:rx + rw].copy()
            if patch.size and not _patch_is_blank(patch):
                patches.append((patch, (rx, 0, rw, bottom - page_h)))
    return patches


_MIN_STROKE_BOX_PX = 8000


def _erase_rects(gray: np.ndarray | None, box: tuple) -> list[tuple]:
    """Erase rects for one text box — stroke-level for big boxes, the box otherwise.

    A big erase box sitting on artwork repainted the whole rectangle with a flat
    LaMa wash (job-2 page 38's drawn `튼다다` over a fire-lit panel came back as a
    grey block). For those, hand the inpainter the ink strokes instead — the
    geometry and the real-worker A/B are documented in `inpaint.stroke_boxes`.
    Small boxes keep the plain rectangle: cheap, and a box that tight around its
    text has no artwork to protect.
    """
    full = tuple(int(v) for v in box)
    if gray is None or box[2] * box[3] < _MIN_STROKE_BOX_PX:
        return [full]
    try:
        return [tuple(int(v) for v in r) for r in stroke_boxes(gray, full)]
    except Exception:
        return [full]


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


def _sibling_boxes(blocks, block) -> list:
    """The other blocks' source boxes — a caption must not be lettered over them.

    Blocks nested inside the source box (its own furigana / sub-lines) are part
    of the same text, not neighbours, so they are not obstacles.
    """
    sx, sy, sw, sh = block.bbox
    out: list = []
    for b in blocks:
        if b is block or b.orientation == "furigana":
            continue
        bx = tuple(int(v) for v in b.bbox)
        if bx[2] <= 0 or bx[3] <= 0:
            continue
        ox = max(0, min(sx + sw, bx[0] + bx[2]) - max(sx, bx[0]))
        oy = max(0, min(sy + sh, bx[1] + bx[3]) - max(sy, bx[1]))
        if ox * oy >= 0.5 * (bx[2] * bx[3]):
            continue
        out.append(bx)
    return out


# Free-floating caption regions must avoid the artwork AND the other text.
#
# `_caption_region` widens a tall-narrow source column so the horizontal English
# has room, but a blind rectangle ignores what it lands on: on job-3 page 19 a
# 112px column's strip was widened 153px to the RIGHT — straight over the
# neighbouring column — and its 1.5x height expansion lifted the lettering out of
# the panel, so two English letterings were printed through each other across the
# panel rule. The strip is now grown from the source box into free space only:
# space the original page leaves un-inked and that no sibling block owns.
_CAPTION_INK = 150          # darker than this is artwork / glyph stroke / panel rule
_CAPTION_RUN = 3            # this many consecutive dark pixels mark a stroke
_CAPTION_DARK_SHARE = 0.06  # ...or this much of the band being dark does
_CAPTION_OBSTACLE_GAP = 4   # keep this much clear of a sibling block's box


def _caption_blocked(gray, obstacles=None, gap: int = _CAPTION_OBSTACLE_GAP) -> np.ndarray:
    """Pixels a caption's lettering may not be drawn over.

    Ink from the original page (artwork, glyph strokes, panel rules) plus every
    sibling block's box: the sibling's source text is erased and lettered
    separately, so English must never be drawn there.
    """
    blocked = np.asarray(gray) < _CAPTION_INK
    H, W = blocked.shape[:2]
    for box in obstacles or ():
        try:
            ox, oy, ow, oh = (int(v) for v in tuple(box)[:4])
        except (TypeError, ValueError):
            continue
        x0, y0 = max(0, ox - gap), max(0, oy - gap)
        x1, y1 = min(W, ox + ow + gap), min(H, oy + oh + gap)
        if x1 > x0 and y1 > y0:
            blocked[y0:y1, x0:x1] = True
    return blocked


def _max_run(mask: np.ndarray) -> np.ndarray:
    """Per-column longest run of True in a 2D mask (row-major scan)."""
    cols = mask.shape[1]
    run = np.zeros(cols, dtype=np.int32)
    best = np.zeros(cols, dtype=np.int32)
    for r in range(mask.shape[0]):
        run = np.where(mask[r], run + 1, 0)
        np.maximum(best, run, out=best)
    return best


def _free_lines(blocked: np.ndarray, lo: int, hi: int, axis: int) -> np.ndarray:
    """True for each line that is free of ink across the band [lo, hi).

    `axis=0` scans a row band and reports per COLUMN; `axis=1` scans a column
    band and reports per ROW. A line is blocked when it carries a stroke
    (`_CAPTION_RUN` consecutive dark pixels) or a lot of darkness
    (`_CAPTION_DARK_SHARE`) — the run test lets dense screentone stay usable as
    lettering space while a 1-2px panel rule, a glyph stroke or a mass of hair
    still stops the strip.
    """
    lo, hi = max(0, lo), min(blocked.shape[axis], hi)
    if hi - lo < 1:
        return np.zeros(0, dtype=bool)
    band = blocked[lo:hi, :] if axis == 0 else blocked[:, lo:hi].T
    runs = _max_run(band)
    share = band.mean(axis=0)
    return ~((runs >= _CAPTION_RUN) | (share >= _CAPTION_DARK_SHARE))


def _run_room(free: np.ndarray, box0: int, box1: int, limit: int) -> int:
    """How far [box0, box1) can grow BOTH ways through contiguous free lines."""
    n = len(free)
    if n == 0:
        return 0
    box0 = max(0, min(box0, n))
    box1 = max(0, min(box1, n))
    if box1 <= box0:
        return 0
    room = 0
    side = 0
    i = box0 - 1
    while i >= 0 and free[i] and side < limit:
        side += 1
        i -= 1
    room = side
    side = 0
    i = box1
    while i < n and free[i] and side < limit:
        side += 1
        i += 1
    return min(room, side, limit)


def _caption_region(bbox: tuple, page_w: int, page_h: int, gray=None,
                    obstacles=None) -> tuple:
    """Region for free-floating text that has no enclosing speech box.

    English is horizontal, so a *vertical* caption (问世间情为何物) needs width, and
    a short-wide footnote needs height for its wrapped lines — but small on-screen
    labels / single characters must NOT be widened across the page (that spilled
    UI text into neighbouring panels). Only tall-narrow text gets the wide strip;
    everything else keeps its own width and just gains vertical room.

    With `gray` (the ORIGINAL page) the strip is also grown into free space only:
    the source box expands symmetrically as far as the page stays un-inked and no
    sibling block owns the space, so the English stays where the source text was
    instead of being lettered over artwork or over the neighbouring column.
    """
    x, y, w, h = bbox
    if h > w * 1.5:
        # Vertical caption (问世间情为何物): English is horizontal, so give it a
        # strip whose width tracks the source text's length (its vertical height
        # is a proxy for char count), capped at 60% page. A fixed 60%-page strip
        # blew up SHORT vertical labels — a 2-char 大吉 (Great fortune) typeset
        # across a page-wide strip became an enormous font.
        #
        # Also capped to the source column's OWN footprint (2.5x its width, plus a
        # little breathing room): a tategaki column of mutter/narration over
        # artwork (job-4 page 10: a 54x346 column) was being lettered clear across
        # the strip and ran into the neighbouring panel's balloon — two texts on
        # top of each other. Keeping the strip near the source keeps the English
        # where the source was; the fit simply wraps at the narrower width.
        nw = max(w, min(int(h * 1.5), int(page_w * 0.6), int(w * 2.5) + 24))
    else:
        nw = w
    nh = max(int(h * 1.5), int(page_h * 0.06))
    if gray is None:  # legacy geometry, no page to check against
        nx = max(0, min(x, page_w - nw))  # keep it on-page
        ny = max(0, y + h // 2 - nh // 2)  # center the strip on the source text
        return (nx, ny, nw, nh)
    blocked = _caption_blocked(gray, obstacles)
    # A stored box can sit outside its page (a box recorded by an older render at
    # a different scale); clamp instead of trusting it, or the run walks off the
    # end of the mask.
    H, W = blocked.shape[:2]
    x, y = max(0, min(x, max(0, W - 1))), max(0, min(y, max(0, H - 1)))
    w, h = max(1, min(w, W - x)), max(1, min(h, H - y))
    max_x, max_y = (nw - w) // 2, (nh - h) // 2
    free_cols = _free_lines(blocked, y, y + h, 0)
    gx = _run_room(free_cols, x, x + w, max_x)
    free_rows = _free_lines(blocked, x - gx, x + w + gx, 1)
    gy = _run_room(free_rows, y, y + h, max_y)
    # second pass: the taller/wider band can only ever shrink the other axis
    gx = min(gx, _run_room(_free_lines(blocked, y - gy, y + h + gy, 0), x, x + w, max_x))
    gy = min(gy, _run_room(_free_lines(blocked, x - gx, x + w + gx, 1), y, y + h, max_y))
    nx = max(0, min(x - gx, page_w - (w + 2 * gx)))
    ny = max(0, min(y - gy, page_h - (h + 2 * gy)))
    return (nx, ny, w + 2 * gx, h + 2 * gy)


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
    llm_timeout: float | None = None,
    llm_max_retries: int | None = None,
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
    # `plain_np`/`plain_image` keep the NATIVE page (no strip) for OCR: PaddleOCR
    # resizes its input internally, so handing it a page with the lookahead strip
    # attached shrinks the effective glyph height and recognition collapses into
    # garbage syllables (see `_ocr_ko_zh_native`). The stitched `image` is still
    # used for blank-check, bubble detection, inpaint and typeset.
    plain_np = image_np
    plain_image = image
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
                boxes = _ocr_ko_zh_native(plain_image, plain_np, lookahead,
                                          gpu_worker_url, lang)
                drop_all_pipelines()  # worker does the OCR now; free local models
            except Exception:
                boxes = None
        if boxes is None:
            boxes = _ocr_ko_zh_native(plain_image, plain_np, lookahead, None, lang,
                                      ocr_fn=read_boxes_text)
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
    # ...then collapse repeats of the SAME utterance the detector emitted for one
    # region (halftone screentone makes the OCR box detector fire 2-6 times with a
    # few px of jitter). Each copy is translated and lettered into the same spot
    # otherwise — job-4 page 118 ended up with five overlapping copies of
    # "SPEAKING OF WHICH," over artwork that holds no text at all.
    blocks = _collapse_duplicate_blocks(blocks)
    if lang == "ja":
        # Balloons holding a lone glyph + furigana are invisible to the text-region
        # detector, so RT-DETR finds the balloon but nothing ever OCRs it and the
        # bubble stays Japanese (job-5 page 3: 「真」/まこと). Recover them by OCR'ing
        # the balloon interior directly — see `_ocr_bubbles_without_text`.
        if bubbles:
            blocks = blocks + _ocr_bubbles_without_text(image_np, bubbles, blocks)
        # Large stylized titles/logos (series title, section headers) are
        # mis-OCR'd by manga-ocr, so leave them untouched — but only for
        # Japanese. Korean/Chinese webtoon dialogue is horizontal and always
        # translated; the height heuristic would wrongly drop a multi-line
        # speech bubble as a "title" (e.g. a 5-line bubble > 15% of the page).
        blocks = _drop_titles(blocks, page_h)
    blocks = _split_bullet_lines(blocks)

    # Already-English signage and one-glyph reads of drawn art are not dialogue —
    # see each guard for the pages that motivated it.
    if lang in ("ko", "zh"):
        blocks = _drop_stub_art(_drop_english_signage(blocks, bubbles), bubbles)

    # One bubble must hold ONE string: several OCR blocks resolving to the same
    # speech bubble would otherwise each be centred into it, lettering English
    # over English (see `_merge_blocks_per_bubble`). Applies to every language:
    # job-3 page 145's starburst balloon held `ヒッ` AND a full sentence, and both
    # were lettered into the balloon on top of each other.
    if bubbles:
        blocks = _merge_blocks_per_bubble(blocks, bubbles)

    # ---- boundary filtering (lookahead / carryover) --------------------------
    # Drop blocks that belong to the next page (entirely in the lookahead strip)
    # or that were already resolved by the previous page (their bottom half was
    # drawn there and carried over here as a paste patch).
    #
    # Text that lives in the lookahead strip is NOT lettered here — but it must
    # still be ERASED. Otherwise it survives into this page's rendered strip, and
    # `_extract_carryover` copies it into the patch handed to the next page, which
    # then pastes its OWN source glyphs back on top of its freshly lettered text.
    # (2026-09-15 job-2 page 41: page 40's region overlapped the boundary, so the
    # patch carried '하고' back over page 41's clean "I'M RETIRING".)
    strip_erase: list[tuple] = []
    if lookahead is not None or carryover:
        carryover_boxes = [tuple(c[1]) for c in (carryover or [])]
        kept: list[TextBlock] = []
        for b in blocks:
            if lookahead is not None and b.bbox[1] >= page_h:
                strip_erase.append(b.bbox)  # next page's text — erase, don't letter
                continue
            if _overlaps_any(b.bbox, carryover_boxes):
                continue  # already lettered by the previous page's patch
            kept.append(b)
        blocks = kept

        # A carryover patch that overlaps text THIS page owns is spurious — the
        # previous page's region bled across the boundary. Pasting it would stamp
        # old (source-bearing) pixels over the lettering drawn below, so drop it.
        carryover = _drop_spurious_carryover(carryover, blocks)

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
            timeout=llm_timeout,
            max_retries=llm_max_retries,
        )

    # ---- resolve typeset targets + erase boxes -------------------------------
    targets: list[tuple[TextBlock, tuple]] = []
    erase: list[tuple] = []
    # Gray copy of the (lookahead-stitched) page for the stroke-level erase masks.
    gray_page = np.asarray(image.convert("L"))
    # Blocks whose region is a speech balloon (not a bare rectangle): the
    # typesetter fits their lettering to the balloon's actual outline.
    shaped: set[int] = set()
    # Regions already assigned on this page. A caption strip must also stay clear
    # of THOSE (not just of the sibling's source box): two columns whose strips
    # each stop short of the other's box still overlap in the gap between them.
    claimed: list = []
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
                erase.extend(_erase_rects(gray_page, b.bbox))
                continue
            if not b.translation:
                continue
            raw = find_parent_bubble(bubbles, b.bbox) if bubbles else None
            if raw is not None:
                # The bubble's bbox, not the tight OCR box: the typesetter fits the
                # lettering to the balloon's own outline (`shapes`), so the text
                # fills the balloon as much as its curve allows without crossing it.
                region = tuple(raw)
                shaped.add(id(b))
            elif _is_drawn_sfx(b):
                # Drawn sound effect / art text with no bubble: its OCR box is the
                # footprint of the lettering on the art, so letter into that box at
                # a size that fills it (see `typeset._draw_box`) instead of
                # treating it like a caption — that shrank a page-wide SFX to a
                # ~21px word via the page-width font cap.
                b.is_sfx = True
                region = b.bbox
            else:
                sb = find_speech_box(image_np, b.bbox)
                if sb:
                    raw = sb
                    region = tuple(sb)  # a real speech box — fit to its outline
                    shaped.add(id(b))
                else:
                    # Free-floating text on artwork (vertical caption or horizontal
                    # footnote) with no enclosing box: English is always horizontal,
                    # so letter it across a generous strip instead of fitting it to
                    # the source text's (tall-narrow or short-wide) box shape.
                    raw = None
                    # The strip may only use space the page leaves un-inked and no
                    # sibling block owns — otherwise the widened caption is lettered
                    # over the neighbouring column's English or over the artwork.
                    region = _caption_region(b.bbox, page_w, page_h, gray=gray_page,
                                             obstacles=_sibling_boxes(blocks, b) + claimed)
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
            claimed.append(tuple(int(v) for v in region))
            erase.extend(_erase_rects(gray_page, b.bbox))
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
                parent = find_parent_bubble(bubbles, b.bbox)
                region = tuple(parent) if parent is not None else None
                _src = "detector-bubble"
                if region is not None:
                    # The balloon's bbox, not an inset rectangle: the typesetter
                    # fits the lettering to the balloon's actual curve (`shapes`),
                    # so a big round bubble is lettered to fill it while an oval or
                    # spiked one is never overrun.
                    shaped.add(id(b))
                # The detector's bubble box is often much TIGHTER than the balloon it
                # names — measured on job-3 p12 it was ~half the area (204x311 for a
                # 307x413 balloon). Fitting the English into that small box forced a
                # small font AND centred the text high in the balloon, leaving its
                # lower half empty. Mirror the ko/zh path: flood the white interior
                # to recover the real balloon, and prefer it when it plausibly
                # belongs to this column (see `_plausible_balloon` — the flood can
                # escape a balloon whose outline has a gap).
                cont = find_container(gray_page, b.bbox)
                _cont_note = "none"
                if cont is not None:
                    if _plausible_balloon(cont, b.bbox, region, page_w, page_h):
                        region = tuple(cont)
                        _src = "container"
                        shaped.add(id(b))
                    else:
                        _cont_note = "rejected-implausible"
                if region is None:
                    # A tall balloon with art drawn inside its lower half splits the
                    # white flood, so neither the detector nor find_container recovers
                    # it — and lettering the tight text box leaves the English
                    # top-aligned in the balloon (job-3 p15). Recover the balloon's
                    # full vertical extent (bridging over the interior art) before
                    # falling back to the free-text strip.
                    gt = find_balloon_gap_tolerant(gray_page, b.bbox)
                    if gt is not None:
                        region = tuple(gt)
                        _src = "gap-tolerant"
                        shaped.add(id(b))
                    else:
                        # Free text / caption: no bubble edge to avoid (see
                        # `_free_text_region` for the tall-narrow widening rule).
                        region = _free_text_region(b.bbox, page_w, page_h, gray=gray_page,
                                                   obstacles=_sibling_boxes(blocks, b) + claimed)
                        _src = "caption-strip"
                fitlog.record_region(b.bbox, _src, region,
                                     {"detector_box": list(parent) if parent else None,
                                      "container": list(cont) if cont else None,
                                      "container_note": _cont_note,
                                      "orientation": b.orientation})
            targets.append((b, region))
            claimed.append(tuple(int(v) for v in region))
            erase.extend(_erase_rects(gray_page, b.bbox))
    else:
        gray = np.asarray(image.convert("L"))
        for b in blocks:
            if b.orientation == "furigana":
                erase.extend(_erase_rects(gray_page, b.bbox))
                continue
            if not b.translation:
                continue
            if is_free_floating(gray, b.bbox):
                continue
            targets.append((b, find_container(gray, b.bbox)))
            erase.extend(_erase_rects(gray_page, b.bbox))

    # ---- inpaint (remote GPU worker → local LaMa) ----------------------------
    # Erase the Korean in carryover regions even if detection missed them, so a
    # pasted English patch never sits on un-erased source text.
    for _patch, cbox in (carryover or []):
        erase.append(tuple(cbox))
    # Erase the NEXT page's text that the lookahead strip pulled in. It is never
    # lettered here, but leaving it un-erased lets `_extract_carryover` copy those
    # source glyphs into the patch handed to the next page (see the boundary
    # filter above). Cropped away from this page's own output, so purely a cleanup.
    for box in strip_erase:
        erase.append(tuple(int(v) for v in box))
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
    # Two stacked balloons detected as ONE merged balloon map two dialogue blocks
    # to the same region; centring each into the whole balloon interleaves their
    # English. Partition the shared balloon's height among them first.
    _partition_shared_bubble(targets)
    regions = {id(b): region for b, region in targets if region is not None}
    emit("typeset")
    result = typeset_page(inpainted, blocks, font_id=font_id, regions=regions, only=only,
                          shapes=shaped, page_label=image_path.split("/")[-1])

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
