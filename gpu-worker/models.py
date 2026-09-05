"""Vision models for the Manga Fill GPU worker.

Self-contained mirror of the app's detection / OCR / inpaint stages, but GPU-
aware (CUDA when available, CPU fallback) and importable without the app's web
stack. Runs the three torch models:

  * RT-DETR-v2 (`ogkalu/comic-text-and-bubble-detector`) — bubble/text detection
  * manga-ocr (`kha-white/manga-ocr-base`)                          — Japanese OCR
  * LaMa (`simple_lama_inpainting`, big-lama)                       — erase text

All three are Apache-2.0. Translation is NOT here — that stays cloud-side in
the app.
"""
from __future__ import annotations

import numpy as np
import torch
from PIL import Image

DETECTOR_ID = "ogkalu/comic-text-and-bubble-detector"


def _detect_backend() -> tuple[str, str]:
    """Return (device, backend) for the torch in this container.

    Torch presents both NVIDIA CUDA and AMD ROCm as the ``cuda`` device — the
    difference is which accelerator the wheel was built against. ROCm builds set
    ``torch.version.hip``; CUDA builds leave it ``None``. Everything else (incl.
    Apple MPS, which these models don't support cleanly) runs on CPU.
    """
    if torch.cuda.is_available():
        if getattr(torch.version, "hip", None):
            return "cuda", "rocm"
        return "cuda", "cuda"
    return "cpu", "cpu"


DEVICE, BACKEND = _detect_backend()


def _lift_torch_load_safety() -> None:
    """Allow legacy pickle (.bin) checkpoints on torch 2.5.1.

    transformers' `check_torch_load_is_safe` hard-blocks `torch.load` below 2.6
    (CVE-2025-32434). This worker is pinned to 2.5.1 because 2.6+ dropped Pascal
    (sm_61) kernels — a hardware constraint, not neglect — and every weight it
    loads is a trusted Apache-2.0 checkpoint (manga-ocr-base, RT-DETR, big-lama).
    Lift the block so manga-ocr's pickle weights still load. (Idempotent; a no-op
    effect on torch >= 2.6.)
    """
    import transformers.modeling_utils as mu
    import transformers.utils.import_utils as iu

    mu.check_torch_load_is_safe = lambda: None
    iu.check_torch_load_is_safe = lambda: None


def _torch_lt_26() -> bool:
    try:
        return tuple(int(x) for x in torch.__version__.split(".")[:2]) < (2, 6)
    except Exception:
        return False


if _torch_lt_26():
    _lift_torch_load_safety()

_det_model = None
_det_processor = None
_mocr = None
_lama = None


# ---------------------------------------------------------------- detection --
def _to_xywh(box) -> tuple:
    x1, y1, x2, y2 = (int(v) for v in box)
    return (x1, y1, x2 - x1, y2 - y1)


def detect_containers(image: Image.Image, threshold: float = 0.2) -> dict:
    """RT-DETR-v2 on a PIL RGB image -> {bubble, text_bubble, text_free} boxes.

    Low threshold (0.2) is deliberate: tiny single-character SFX sit at
    ~0.3-0.45 confidence; the downstream `_has_japanese` filter rejects
    false positives, so a low bar is safe.
    """
    global _det_model, _det_processor
    if _det_model is None:
        from transformers import RTDetrV2ForObjectDetection, RTDetrImageProcessor

        _det_processor = RTDetrImageProcessor.from_pretrained(DETECTOR_ID)
        _det_model = RTDetrV2ForObjectDetection.from_pretrained(DETECTOR_ID).to(DEVICE)
        _det_model.eval()

    w, h = image.size
    inputs = _det_processor(images=image, return_tensors="pt")
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = _det_model(**inputs)
    res = _det_processor.post_process_object_detection(
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


# ---------------------------------------------------------------------- OCR --
def ocr_crop(image: np.ndarray, bbox: tuple) -> tuple[str, float | None]:
    """OCR a cropped region. Returns (jp_text, None). manga-ocr emits no score.

    Clamps degenerate / out-of-bounds boxes to the image and treats a zero-area
    crop as empty text (a degenerate detector box would otherwise crash the ViT
    with "shape '[1, 3, 224, 224]' is invalid for input of size 0").
    """
    global _mocr
    if _mocr is None:
        from manga_ocr import MangaOcr

        _mocr = MangaOcr()  # auto-selects CUDA when available
    x, y, w, h = bbox
    H, W = image.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return "", None
    crop = Image.fromarray(image[y0:y1, x0:x1])
    text = _mocr(crop)
    return (text or "").strip(), None


def _has_japanese(text: str) -> bool:
    """True if `text` has kana/kanji/CJK punct — skips watermarks & page numbers."""
    return any(
        ("\u3040" <= ch <= "\u30ff")
        or ("\u4e00" <= ch <= "\u9fff")
        or ("\u3000" <= ch <= "\u303f")
        for ch in text
    )


# -------------------------------------------------------- multilingual OCR --
_paddle_pipelines: dict[str, object] = {}

_PADDLE_LANGS = {"ko": "korean", "zh": "ch"}


def ocr_multilingual_blocks(image: Image.Image, lang: str) -> list[dict]:
    """PaddleOCR (PP-OCRv5/v6) full-pipeline detect+recognize for ko/zh.

    Mirrors the app's `app/pipeline/ocr_multilingual.py::read_boxes_text` — keep
    them in lockstep. Reads VERTICAL text natively via the textline-orientation
    classifier. Returns [{"bbox": [x,y,w,h], "text": str, "confidence": float}]
    with boxes in ORIGINAL image coords (`dt_polys`, before any orientation
    rotation). Runs on CPU through the ONNX runtime engine (paddle native CPU
    inference is broken: PIR/oneDNN).
    """
    global _paddle_pipelines
    if lang not in _paddle_pipelines:
        from paddleocr import PaddleOCR  # heavy import — lazy on purpose

        _paddle_pipelines[lang] = PaddleOCR(
            lang=_PADDLE_LANGS[lang],
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            device="cpu",
            engine="onnxruntime",
        )
    arr = np.asarray(image.convert("RGB"))
    out: list[dict] = []
    for r in _paddle_pipelines[lang].predict(arr):
        for poly, text, conf in zip(r["dt_polys"], r["rec_texts"], r["rec_scores"]):
            text = (text or "").strip()
            if not text:
                continue
            pts = np.asarray(poly)
            x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
            x1, y1 = int(pts[:, 0].max()), int(pts[:, 1].max())
            if x1 <= x0 or y1 <= y0:
                continue
            out.append({"bbox": [x0, y0, x1 - x0, y1 - y0], "text": text,
                        "confidence": float(conf)})
    return out


# ------------------------------------------------------------------ inpaint --
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
    """Erase text inside `boxes` from `image` at native resolution (LaMa)."""
    global _lama
    if _lama is None:
        from simple_lama_inpainting import SimpleLama

        _lama = SimpleLama(device=torch.device(DEVICE))

    if not boxes:
        return image.convert("RGB")

    img = image.convert("RGB")
    w, h = img.size
    out = img.copy()

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

        cboxes = [
            (bx - x0, by - y0, bbw, bbh)
            for (bx, by, bbw, bbh) in boxes
            if bx < x1 and bx + bbw > x0 and by < y1 and by + bbh > y0
        ]

        scale = min(1.0, max_crop / max(cw, ch))
        if scale < 1.0:
            dw, dh = max(1, int(cw * scale)), max(1, int(ch * scale))
            small = crop.resize((dw, dh), Image.LANCZOS)
            sboxes = [(int(a * scale), int(b * scale), int(c * scale), int(d * scale))
                      for (a, b, c, d) in cboxes]
            mask_small = _mask_from_boxes((dw, dh), sboxes, dilate)
            lama_full = _lama(small, mask_small).convert("RGB").resize((cw, ch), Image.LANCZOS)
            mask_full = mask_small.resize((cw, ch), Image.NEAREST)
        else:
            mask_full = _mask_from_boxes((cw, ch), cboxes, dilate)
            lama_full = _lama(crop, mask_full).convert("RGB").resize((cw, ch), Image.LANCZOS)

        mask_arr = np.asarray(mask_full)[:, :, None] > 0
        res = Image.fromarray(
            np.where(mask_arr, np.asarray(lama_full), crop_np).astype(np.uint8)
        )
        out.paste(res, (x0, y0))

    return out


# ------------------------------------------------------------------ warmup --
def warmup() -> None:
    """Force-load all three models (used at Docker build to bake weights in)."""
    import io

    detect_containers(Image.new("RGB", (64, 64), "white"))
    ocr_crop(np.full((32, 32, 3), 255, dtype=np.uint8), (0, 0, 32, 32))
    inpaint_text(Image.new("RGB", (64, 64), "white"), [(8, 8, 16, 16)])
    print(f"warmup complete on device={DEVICE}")
