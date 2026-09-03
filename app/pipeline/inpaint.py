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
