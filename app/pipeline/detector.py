"""Bubble + text detection via ogkalu RT-DETR-v2 (Apache-2.0).

A trained manga detector that returns three region types in one pass:
  * bubble      — speech bubble / caption box (the typeset target)
  * text_bubble — text inside a bubble (the dialogue to OCR + translate)
  * text_free   — free-floating text / watermarks (left untouched)

This replaces both the PP-OCRv5 text detector (which split multi-column vertical
text into fragments) and the white-flood-fill bubble heuristic (which broke on
"bleeding" bubbles whose white interior merges with the panel gutter).

All boxes are returned as (x, y, w, h) in image pixel coords. Lazy-loaded: the
model is only pulled in on the first `detect_containers` call.
"""
from __future__ import annotations

from PIL import Image

from .device import get_device

MODEL_ID = "ogkalu/comic-text-and-bubble-detector"

_model = None
_processor = None
_model_device = None


def _get():
    global _model, _processor, _model_device
    device = get_device()
    if _model is None or _model_device != device:
        from transformers import RTDetrV2ForObjectDetection, RTDetrImageProcessor

        _processor = RTDetrImageProcessor.from_pretrained(MODEL_ID)
        _model = RTDetrV2ForObjectDetection.from_pretrained(MODEL_ID).to(device)
        _model_device = device
    return _model, _processor


def _to_xywh(box) -> tuple:
    x1, y1, x2, y2 = (int(v) for v in box)
    return (x1, y1, x2 - x1, y2 - y1)


# A `text_free` region that is NARROW AND TALL is where the detector fires on halftone
# screentone: manga-ocr then reads a plausible phrase out of the dots and the app
# letters it onto the artwork (job-4 pages 118/144/43/152/47 — page 118 got five
# overlapping copies of one line drawn onto a clean panel). Measured over nine such
# pages at a 0.05 threshold: every phantom detection is `text_free`, 13-40px wide,
# 81-560px tall and scores 0.20-0.43, while genuine free text on the same pages reaches
# 0.94 and only dips below 0.44 when it is compact or wide (a 34x130 chart caption at
# 0.451, a 51x154 drawn SFX at 0.253, 51px-wide mutter columns at 0.231; a real 31x76
# `うん` in a bubble sits just under the height bar). The 48px ceiling splits the measured
# gap — phantoms run 13-48px wide, the narrowest REAL free text in the sample is 51px —
# so narrow-and-tall free text has to clear a real score; everything else keeps the low
# 0.2 bar that
# exists so tiny single-character SFX (ほえ, はっ, コヒュ at 0.3-0.45) survive.
# Mirrored in gpu-worker/models.py — keep the two in step.
TEXT_FREE_TALL_MIN_SCORE = 0.44
TEXT_FREE_TALL_MAX_W = 48
TEXT_FREE_TALL_MIN_H = 80


def is_halftone_prone_text_free(w: int, h: int, score: float | None) -> bool:
    """True if a `text_free` region of this size/score is more likely screentone
    than text (see the constants above for the measurements behind the numbers)."""
    if score is None:
        return False
    return (w <= TEXT_FREE_TALL_MAX_W and h >= TEXT_FREE_TALL_MIN_H
            and score < TEXT_FREE_TALL_MIN_SCORE)


def detect_containers(image: Image.Image, threshold: float = 0.2) -> dict:
    """Run the detector on a PIL RGB image.

    Returns {"bubble": [...], "text_bubble": [...], "text_free": [...],
    "dropped_halftone": n}.

    Threshold is deliberately low (0.2): tiny single-character SFX (ほえ, はっ,
    コヒュ) are only ~0.3-0.45 confidence and get dropped at 0.4. The OCR +
    `_has_japanese` filter downstream rejects false positives (art/texture), so a
    low detection threshold is safe — EXCEPT for narrow-and-tall `text_free`
    regions, which is exactly where the detector fires on halftone and the OCR
    hallucinates a phrase out of the dots; those must clear a higher score
    (`is_halftone_prone_text_free`).
    """
    model, processor = _get()
    w, h = image.size
    inputs = processor(images=image, return_tensors="pt")
    if get_device() == "cuda":
        inputs = {k: v.to("cuda") for k, v in inputs.items()}
    outputs = model(**inputs)
    res = processor.post_process_object_detection(
        outputs, target_sizes=[(h, w)], threshold=threshold
    )[0]
    out: dict = {"bubble": [], "text_bubble": [], "text_free": [],
                 "dropped_halftone": 0}
    for label, box, score in zip(res["labels"], res["boxes"], res["scores"]):
        cls = int(label)
        xywh = _to_xywh(box)
        if cls == 0:
            out["bubble"].append(xywh)
        elif cls == 1:
            out["text_bubble"].append(xywh)
        elif cls == 2:
            if is_halftone_prone_text_free(xywh[2], xywh[3], float(score)):
                out["dropped_halftone"] += 1
                continue
            out["text_free"].append(xywh)
    return out


def _contains(box: tuple, x: int, y: int) -> bool:
    x1, y1, w, h = box
    return x1 <= x <= x1 + w and y1 <= y <= y1 + h


def find_parent_bubble(bubbles: list[tuple], bbox: tuple) -> tuple | None:
    """Return the (x,y,w,h) bubble containing the center of `bbox`, else None."""
    x, y, w, h = bbox
    cx, cy = x + w // 2, y + h // 2
    hits = [b for b in bubbles if _contains(b, cx, cy)]
    if not hits:
        return None
    return min(hits, key=lambda b: b[2] * b[3])
