"""Manga Fill GPU worker — FastAPI service exposing the vision models over HTTP.

Endpoints:
  GET  /health      -> device + version (Manga Fill probes this before use)
  POST /detect-ocr  -> detect bubbles + OCR text in one pass
  POST /inpaint     -> erase text boxes via LaMa

Designed as a drop-in accelerator for the Manga Fill app: the app calls these
when `gpu_worker_url` is set, and falls back to its own CPU models otherwise.
Single uvicorn worker on purpose — model weights live in process memory, not a
shared store.
"""
from __future__ import annotations

import io
import json

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image

from models import BACKEND, DEVICE, _has_japanese, detect_containers, inpaint_text, ocr_crop

__version__ = "0.2.2"

app = FastAPI(title="Manga Fill GPU worker", version=__version__)


def _load(upload: UploadFile) -> Image.Image:
    try:
        data = upload.file.read()
    finally:
        upload.file.close()
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:  # bad image
        raise HTTPException(status_code=400, detail=f"invalid image: {e}")


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _containment(a: tuple, b: tuple) -> float:
    """Fraction of the *smaller* box covered by the intersection (nested dedup)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    smaller = min(aw * ah, bw * bh)
    return inter / smaller if smaller else 0.0


def _orientation(w: int, h: int) -> str:
    """Classify a free-text region by shape (mirrors the app's render._orientation)."""
    if w <= 16 and h > w * 2:
        return "furigana"
    if h > w * 1.5:
        return "vertical"
    return "horizontal"


def _overlaps(box: tuple, seen: list) -> bool:
    return any(_iou(box, s) > 0.5 or _containment(box, s) > 0.85 for s in seen)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": DEVICE, "backend": BACKEND, "version": __version__}


@app.post("/detect-ocr")
def detect_ocr(image: UploadFile = File(...)) -> dict:
    """Detect bubbles + OCR text. Returns {"bubble": [...], "blocks": [...]}.

    Mirrors the app's RT-DETR branch exactly: `text_bubble` regions are OCR'd
    (dropping empty / single-char / non-Japanese hits), and `text_free` regions
    are split by shape — vertical keeps the full region, horizontal keeps the
    individual lines — with nested/overlapping detections deduped so the same
    stat column isn't returned both whole and line-by-line (which made the app
    typeset English on top of English). Blocks are sorted top-to-bottom, then
    right-to-left within a row.
    """
    img = _load(image)
    det = detect_containers(img)
    image_np = np.asarray(img)

    blocks: list[dict] = []
    seen: list[tuple] = []

    # 1) dialogue inside bubbles — always vertical, keep the full region
    for (x, y, w, h) in sorted(det["text_bubble"], key=lambda b: -(b[2] * b[3])):
        if _overlaps((x, y, w, h), seen):
            continue
        text, _conf = ocr_crop(image_np, (x, y, w, h))
        if not text or len(text.strip()) <= 1 or not _has_japanese(text):
            continue
        blocks.append({"bbox": [x, y, w, h], "text": text, "orientation": "vertical"})
        seen.append((x, y, w, h))

    # 2) free text — split by shape, dedup each group with the right strategy
    free = det["text_free"]
    free_vertical = [(x, y, w, h) for (x, y, w, h) in free if _orientation(w, h) != "horizontal"]
    free_horizontal = [(x, y, w, h) for (x, y, w, h) in free if _orientation(w, h) == "horizontal"]

    for (x, y, w, h) in sorted(free_vertical, key=lambda b: -(b[2] * b[3])):
        if _overlaps((x, y, w, h), seen):
            continue
        text, _conf = ocr_crop(image_np, (x, y, w, h))
        if not text or not _has_japanese(text):
            continue
        blocks.append({"bbox": [x, y, w, h], "text": text, "orientation": _orientation(w, h)})
        seen.append((x, y, w, h))

    for (x, y, w, h) in sorted(free_horizontal, key=lambda b: b[2] * b[3]):
        if _overlaps((x, y, w, h), seen):
            continue
        text, _conf = ocr_crop(image_np, (x, y, w, h))
        if not text or not _has_japanese(text):
            continue
        blocks.append({"bbox": [x, y, w, h], "text": text, "orientation": "horizontal"})
        seen.append((x, y, w, h))

    blocks.sort(key=lambda b: (b["bbox"][1], -b["bbox"][0]))
    return {"bubble": det["bubble"], "blocks": blocks}


@app.post("/inpaint")
def inpaint(
    image: UploadFile = File(...),
    boxes: str = Form(...),  # JSON list of [x, y, w, h]
) -> Response:
    """Erase the given boxes from the image. Returns the inpainted PNG."""
    img = _load(image)
    try:
        parsed = json.loads(boxes)
        box_list = [tuple(int(v) for v in b) for b in parsed]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid boxes JSON: {e}")

    out = inpaint_text(img, box_list)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
