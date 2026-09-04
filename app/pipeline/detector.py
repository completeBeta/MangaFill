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


def detect_containers(image: Image.Image, threshold: float = 0.2) -> dict:
    """Run the detector on a PIL RGB image.

    Returns {"bubble": [(x,y,w,h)...], "text_bubble": [...], "text_free": [...]}.

    Threshold is deliberately low (0.2): tiny single-character SFX (ほえ, はっ,
    コヒュ) are only ~0.3-0.45 confidence and get dropped at 0.4. The OCR +
    `_has_japanese` filter downstream rejects false positives (art/texture), so a
    low detection threshold is safe.
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
    out: dict = {"bubble": [], "text_bubble": [], "text_free": []}
    for label, box in zip(res["labels"], res["boxes"]):
        cls = int(label)
        if cls == 0:
            out["bubble"].append(_to_xywh(box))
        elif cls == 1:
            out["text_bubble"].append(_to_xywh(box))
        elif cls == 2:
            out["text_free"].append(_to_xywh(box))
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
