"""Inpaint — erase original Japanese text with LaMa (Apache-2.0).

Loads big-lama via simple-lama-inpainting. Its `pillow<10` metadata pin is stale —
it runs fine on pillow>=10 (verified). Mask = solid dilated boxes; LaMa reconstructs
the erased region (bubble interior -> white, artwork -> plausible fill).

The page is NOT downscaled. Only each text region is cropped out (with generous
padding so LaMa sees surrounding context), inpainted at native resolution, and
pasted back. Every pixel outside the erased boxes is byte-for-byte identical to the
source. Memory is bounded by the largest crop, not the page size — a single huge
merged crop (rare) is the only thing that ever downscales, and only that crop, not
the page.
"""
from __future__ import annotations

from PIL import Image
import numpy as np

from .device import get_device

_lama = None
_lama_device = None


def _get_lama():
    global _lama, _lama_device
    device = get_device()
    if _lama is None or _lama_device != device:
        from simple_lama_inpainting import SimpleLama  # lazy: downloads big-lama on first use

        # SimpleLama's `device` accepts "cuda"/"cpu" (map_location + model.to both).
        _lama = SimpleLama(device=device)
        _lama_device = device
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


def _merge_rects(rects: list[tuple]) -> list[tuple]:
    """Union overlapping (x0, y0, x1, y1) rects so each pixel is inpainted once."""
    rects = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[tuple] = []
        used = [False] * len(rects)
        for i in range(len(rects)):
            if used[i]:
                continue
            cur = list(rects[i])
            used[i] = True
            for j in range(i + 1, len(rects)):
                if used[j]:
                    continue
                r = rects[j]
                if not (cur[2] < r[0] or r[2] < cur[0] or cur[3] < r[1] or r[3] < cur[1]):
                    cur[0] = min(cur[0], r[0])
                    cur[1] = min(cur[1], r[1])
                    cur[2] = max(cur[2], r[2])
                    cur[3] = max(cur[3], r[3])
                    used[j] = True
                    changed = True
            out.append(tuple(cur))
        rects = out
    return rects


def _coarse_background(gray: np.ndarray, cell: int = 16) -> np.ndarray:
    """Per-tile median background estimate, upsampled to the input size.

    A single median over the whole box fails on a gradient or photo background
    (a dark panel with a lit edge, a webtoon sky), so the background is
    estimated per `cell`-sized tile instead. Pure numpy — the app image ships
    numpy + PIL, no scipy.
    """
    h, w = gray.shape
    ch, cw = max(1, min(cell, h)), max(1, min(cell, w))
    ny, nx = max(1, h // ch), max(1, w // cw)
    tiles = gray[: ny * ch, : nx * cw].reshape(ny, ch, nx, cw)
    med = np.median(tiles, axis=(1, 3))
    bg = np.repeat(np.repeat(med, ch, axis=0), cw, axis=1)
    if bg.shape != gray.shape:
        bg = np.pad(bg, ((0, h - bg.shape[0]), (0, w - bg.shape[1])), mode="edge")
    return bg


def _label_runs(mask: np.ndarray, min_ov: float = 0.25) -> tuple[np.ndarray, int]:
    """Label 4-connected blobs by chaining per-row runs (no scipy in this image).

    Returns (label image, label count); background pixels are -1.
    """
    h, w = mask.shape
    lab = -np.ones((h, w), dtype=np.int32)
    nxt = 0
    prev: list[tuple[int, int, int]] = []
    for y in range(h):
        idx = np.flatnonzero(np.diff(np.r_[0, mask[y].view(np.int8), 0]))
        cur: list[tuple[int, int, int]] = []
        for s, e in zip(idx[0::2], idx[1::2]):
            tag = -1
            for (ps, pe, pl) in prev:
                if min(e, pe) - max(s, ps) > min_ov * min(e - s, pe - ps):
                    tag = pl
                    break
            if tag < 0:
                tag = nxt
                nxt += 1
            lab[y, s:e] = tag
            cur.append((s, e, tag))
        prev = cur
    return lab, nxt


def _fill_holes(mask: np.ndarray, max_frac: float = 0.12, min_px: int = 4000) -> np.ndarray:
    """Fill background regions fully enclosed by the mask.

    A drawn glyph is an OUTLINE: its interior is a large flat area of the glyph's
    own colour, which the tile-median background estimate reads as background.
    Left alone, the erase mask covers only the outline and the glyph's body
    survives as a ghost — measured on job-2 page 84, whose erased `조~으~옹..` left
    a dark blob sitting in the blue sky. The cap keeps genuinely enclosed ARTWORK
    out of the mask: page 98's lettuce poster is enclosed by its own border and is
    an order of magnitude bigger than any glyph interior, and filling it erased the
    whole panel.
    """
    if not mask.any():
        return mask
    bg_lab, n = _label_runs(~mask)
    if n == 0:
        return mask
    border = np.unique(np.concatenate([bg_lab[0, :], bg_lab[-1, :], bg_lab[:, 0], bg_lab[:, -1]]))
    outside = np.zeros(n, dtype=bool)
    outside[border[border >= 0]] = True
    cap = max(min_px, int(max_frac * mask.size))
    uniq, counts = np.unique(bg_lab[bg_lab >= 0], return_counts=True)
    fillable = {int(u) for u, c in zip(uniq, counts) if not outside[u] and c <= cap}
    if not fillable:
        return mask
    holes = np.isin(bg_lab, list(fillable))
    return mask | holes




def _ink_mask(gray: np.ndarray, box: tuple, thresh: int = 32, fill_holes: bool = True) -> np.ndarray:
    """Boolean mask of the glyph strokes inside `box` (dark or light on either
    background): pixels that differ from the local background estimate."""
    x, y, w, h = [int(v) for v in box]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(gray.shape[1], x + w), min(gray.shape[0], y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return np.zeros((0, 0), dtype=bool)
    crop = gray[y0:y1, x0:x1].astype(np.float32)
    ink = np.abs(crop - _coarse_background(crop)) > thresh
    return _fill_holes(ink) if fill_holes else ink


def _blob_boxes(ink: np.ndarray, min_px: int, min_ov: float = 0.25) -> list[tuple]:
    """Tight bboxes of the ink blobs at least `min_px` pixels big.

    `min_px` is what separates a glyph stroke from the background texture a coarse
    background estimate leaves behind: measured on job-2 page 39's drawn SFX over
    fire art, 16 blobs above the floor carried the lettering while 388 blobs below
    it were shading noise.
    """
    lab, n = _label_runs(ink, min_ov)
    if n == 0:
        return []
    ys, xs = np.nonzero(lab >= 0)
    tags = lab[ys, xs]
    order = np.argsort(tags, kind="stable")
    tags, ys, xs = tags[order], ys[order], xs[order]
    uniq, starts = np.unique(tags, return_index=True)
    ends = np.r_[starts[1:], tags.size]
    out: list[tuple] = []
    for s, e in zip(starts, ends):
        if e - s < min_px:
            continue
        bx, by = xs[s:e], ys[s:e]
        out.append((int(bx.min()), int(by.min()), int(bx.max() - bx.min() + 1), int(by.max() - by.min() + 1)))
    return out



def _tile_rects(mask: np.ndarray, tile: int = 8, grow: int = 1) -> list[tuple]:
    """Approximate a pixel mask with runs of `tile`-sized cells.

    The inpainter's contract is rectangles, so a mask has to be turned back into
    rects. Per-blob bounding boxes were tried first and were too coarse — a
    glyph-shaped blob's bbox refills the gaps between strokes — while row bands
    spanned the whole box. Cell runs follow the mask (and a `grow`-cell skirt),
    which is what makes the stroke mask faithful: measured on job-2 page 38's
    drawn SFX over a fire-lit panel, cells cover ~25% of the box where the old
    box mask covered 100%, and every stroke pixel is inside a cell.
    """
    h, w = mask.shape
    nh, nw = h // tile, w // tile
    if nh < 1 or nw < 1:
        return []
    cells = mask[: nh * tile, : nw * tile].reshape(nh, tile, nw, tile).any(axis=(1, 3))
    for _ in range(max(0, grow)):
        grown = cells.copy()
        grown[1:, :] |= cells[:-1, :]
        grown[:-1, :] |= cells[1:, :]
        grown[:, 1:] |= cells[:, :-1]
        grown[:, :-1] |= cells[:, 1:]
        cells = grown
    rects: list[tuple] = []
    for cy in range(nh):
        row = cells[cy]
        idx = np.flatnonzero(np.diff(np.r_[0, row.view(np.int8), 0]))
        for s, e in zip(idx[0::2], idx[1::2]):
            x0 = int(s) * tile
            x1 = min(w, int(e) * tile)
            y0 = int(cy) * tile
            # extend the run downwards while the cell run stays the same — one
            # rect per text line instead of one per 8px row keeps the payload
            # small (the inpainter dilates and unions rects anyway)
            y1 = y0 + tile
            while y1 // tile < nh:
                nxt = cells[y1 // tile]
                if nxt[s:e].all() and not nxt[:s].any() and not nxt[e:].any():
                    y1 += tile
                else:
                    break
            rects.append((x0, y0, x1 - x0, min(y1 - y0, h - y0)))
    return rects


def stroke_boxes(
    gray: np.ndarray,
    box: tuple,
    thresh: int = 45,
    grow: int = 24,
    tile: int = 8,
    max_rects: int = 400,
    max_fill: float = 0.75,
    min_blob_frac: float = 0.0006,
    min_blob_px: int = 40,
) -> list[tuple]:
    """Tight rects hugging the ink inside `box` — an art-preserving erase mask.

    The erase mask used to be each block's full rectangle, so a block sitting on
    artwork repainted that whole rectangle with a flat LaMa wash: job-2 page 38's
    drawn `튼다다` (502x735 over a fire-lit panel) came back as a grey block, and
    page 56's narration box + drawn SFX merged into one 464x656 box that erased
    the artwork behind both. Measured on those real cases the ink is only 9-19%
    of the box, so masking the strokes instead of the rectangle leaves the
    artwork behind them intact — verified with a real LaMa A/B on the GPU worker:
    the fire-lit building, the sky gradient and the shop interior all survive
    where the box mask flattened them.

    Pipeline: tile-median background → ink mask (high-contrast strokes) → fill
    small enclosed holes (a drawn glyph's body is read as background otherwise,
    leaving the glyph as a ghost) → cell runs with a `grow`-pixel skirt.

    Returns `[box]` unchanged when the ink fills most of the box (nothing to
    gain) or when the decomposition degenerates, so the worst case is exactly
    today's behaviour.
    """
    x, y, w, h = [int(v) for v in box]
    ink = _ink_mask(gray, box, thresh)
    if ink.size == 0:
        return [tuple(int(v) for v in box)]
    full = tuple(int(v) for v in box)
    if ink.mean() >= max_fill:
        return [full]

    # drop texture speckle: keep blobs that look like strokes, not shading noise
    keep_min = max(min_blob_px, int(min_blob_frac * w * h))
    blobs = _blob_boxes(ink, keep_min)
    if blobs:
        clean = np.zeros_like(ink)
        for (bx, by, bw, bh) in blobs:
            clean[by:by + bh, bx:bx + bw] = ink[by:by + bh, bx:bx + bw]
        ink = clean if clean.mean() > 0.002 else ink

    rects = _tile_rects(ink, tile=tile, grow=max(1, grow // tile))
    if not rects or len(rects) > max_rects:
        return [full]
    rects = [(max(0, rx + x), max(0, ry + y), rw, rh)
             for (rx, ry, rw, rh) in rects
             if rx + x < gray.shape[1] and ry + y < gray.shape[0]]
    covered = sum(rw * rh for _rx, _ry, rw, rh in rects)
    if covered >= max_fill * (w * h):
        return [full]
    return rects





def inpaint_text(
    image: Image.Image,
    boxes: list[tuple],
    dilate: int = 5,
    pad_ratio: float = 0.35,
    min_pad: int = 32,
    max_crop: int = 1400,
) -> Image.Image:
    """Erase text inside `boxes` from `image`, at native resolution. Returns a new RGB image."""
    if not boxes:
        return image.convert("RGB")

    lama = _get_lama()
    img = image.convert("RGB")
    w, h = img.size
    out = img.copy()

    # Padded crop per box: pad proportional to box size so LaMa has context.
    rects = []
    for (x, y, bw, bh) in boxes:
        pad = max(min_pad, int(max(bw, bh) * pad_ratio))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + bw + pad)
        y1 = min(h, y + bh + pad)
        rects.append((x0, y0, x1, y1))

    for (x0, y0, x1, y1) in _merge_rects(rects):
        cw, ch = x1 - x0, y1 - y0
        crop = img.crop((x0, y0, x1, y1))
        crop_np = np.asarray(crop)

        # Boxes intersecting this crop, in crop coordinates.
        cboxes = [
            (bx - x0, by - y0, bbw, bbh)
            for (bx, by, bbw, bbh) in boxes
            if bx < x1 and bx + bbw > x0 and by < y1 and by + bbh > y0
        ]

        # Safety valve only: if one merged crop is still huge, downscale *that crop*
        # for LaMa, but still composite against the ORIGINAL full-res crop so only the
        # masked region is ever replaced.
        scale = min(1.0, max_crop / max(cw, ch))
        if scale < 1.0:
            dw, dh = max(1, int(cw * scale)), max(1, int(ch * scale))
            small = crop.resize((dw, dh), Image.LANCZOS)
            sboxes = [(int(a * scale), int(b * scale), int(c * scale), int(d * scale))
                      for (a, b, c, d) in cboxes]
            mask_small = _mask_from_boxes((dw, dh), sboxes, dilate)
            lama_full = lama(small, mask_small).convert("RGB").resize((cw, ch), Image.LANCZOS)
            mask_full = mask_small.resize((cw, ch), Image.NEAREST)
        else:
            mask_full = _mask_from_boxes((cw, ch), cboxes, dilate)
            # LaMa pads input to a multiple of 8; resize its output back to the crop.
            lama_full = lama(crop, mask_full).convert("RGB").resize((cw, ch), Image.LANCZOS)

        # Composite: LaMa only inside the mask; original pixels everywhere else.
        mask_arr = np.asarray(mask_full)[:, :, None] > 0
        res = Image.fromarray(
            np.where(mask_arr, np.asarray(lama_full), crop_np).astype(np.uint8)
        )
        out.paste(res, (x0, y0))

    return out
