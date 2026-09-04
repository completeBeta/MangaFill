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

__version__ = "0.2.0"

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": DEVICE, "backend": BACKEND, "version": __version__}


@app.post("/detect-ocr")
def detect_ocr(image: UploadFile = File(...)) -> dict:
    """Detect bubbles + OCR text. Returns {"bubble": [...], "blocks": [...]}.

    Mirrors the app's RT-DETR branch exactly: `text_bubble` regions are OCR'd
    (dropping empty / single-char hits), `text_free` regions are OCR'd and kept
    only if they contain Japanese (skips watermarks / page numbers). Blocks are
    sorted top-to-bottom, then right-to-left within a row.
    """
    img = _load(image)
    det = detect_containers(img)
    image_np = np.asarray(img)

    blocks: list[dict] = []
    seen: list[tuple] = []

    for (x, y, w, h) in det["text_bubble"]:
        if any(_iou((x, y, w, h), s) > 0.5 for s in seen):
            continue
        text, _conf = ocr_crop(image_np, (x, y, w, h))
        if not text or len(text.strip()) <= 1:
            continue
        blocks.append({"bbox": [x, y, w, h], "text": text, "orientation": "vertical"})
        seen.append((x, y, w, h))

    for (x, y, w, h) in det["text_free"]:
        if any(_iou((x, y, w, h), s) > 0.5 for s in seen):
            continue
        text, _conf = ocr_crop(image_np, (x, y, w, h))
        if not text or not _has_japanese(text):
            continue
        blocks.append({"bbox": [x, y, w, h], "text": text, "orientation": "vertical"})
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
