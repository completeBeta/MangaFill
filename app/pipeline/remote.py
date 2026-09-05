"""Client for the Manga Fill GPU worker (optional remote accelerator).

When `gpu_worker_url` is set and the worker is reachable, the pipeline offloads
detect+OCR and inpaint to the GPU host; on any failure it silently falls back to
the local CPU models. The worker speaks a tiny two-endpoint contract:

    POST /detect-ocr  (multipart `image`)            -> {"bubble": [...], "blocks": [...]}
    POST /inpaint     (multipart `image` + `boxes`)  -> PNG image bytes
"""
from __future__ import annotations

import io
import json

import httpx
from PIL import Image

# detection + OCR can take a while on first load (weights already baked in the
# image, but RT-DETR + manga-ocr + LaMa inference over a full page is seconds).
TIMEOUT = 300.0


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _png_bytes(image: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


def remote_detect_ocr(image: Image.Image, worker_url: str) -> dict:
    """Run detect + OCR on the worker. Returns {"bubble": [...], "blocks": [...]}."""
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(
            _url(worker_url, "/detect-ocr"),
            files={"image": ("page.png", _png_bytes(image), "image/png")},
        )
        r.raise_for_status()
        return r.json()


def remote_ocr_multilingual(
    image: Image.Image, worker_url: str, lang: str
) -> list[tuple]:
    """OCR a Korean/Chinese page on the worker (PaddleOCR PP-OCRv5/v6).

    Returns [(x, y, w, h), text, confidence] — the same shape as the app's
    `read_boxes_text`, so the caller can feed either source through one
    block-building path.
    """
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(
            _url(worker_url, "/ocr-multilingual"),
            files={"image": ("page.png", _png_bytes(image), "image/png")},
            data={"lang": lang},
        )
        r.raise_for_status()
        out = []
        for b in r.json().get("blocks", []):
            x, y, w, h = b["bbox"]
            out.append(((x, y, w, h), b.get("text", ""), float(b.get("confidence", 0.0))))
        return out


def remote_inpaint(image: Image.Image, boxes: list[tuple], worker_url: str) -> Image.Image:
    """Erase `boxes` via the worker's LaMa. Returns the inpainted RGB image."""
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(
            _url(worker_url, "/inpaint"),
            files={"image": ("page.png", _png_bytes(image), "image/png")},
            data={"boxes": json.dumps([list(b) for b in boxes])},
        )
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
