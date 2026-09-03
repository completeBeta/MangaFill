"""Device selection for the local vision models (detect / OCR / inpaint).

The pipeline can run its vision models three ways:

  * **local CPU** — default; works everywhere.
  * **local GPU** — if the host has CUDA and a CUDA-enabled torch, the models run
    on the GPU directly (no separate worker needed).
  * **external GPU worker** — `gpu_worker_url` offloads detect+OCR/inpaint to a
    separate service (`remote.py`); handled in `render.py`, not here.

`set_device()` is called once at startup from `settings.device`
(``auto`` / ``cpu`` / ``cuda``). ``auto`` uses CUDA when available and falls back
to CPU otherwise — the safe default for both CPU-only and GPU hosts.
"""
from __future__ import annotations

_DEVICE = "cpu"


def resolve(configured: str) -> str:
    """Map a configured device name to a concrete torch device string."""
    c = (configured or "auto").strip().lower()
    if c == "cpu":
        return "cpu"
    try:
        import torch

        if c in ("auto", "cuda", "gpu") and torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def set_device(configured: str) -> None:
    global _DEVICE
    _DEVICE = resolve(configured)


def get_device() -> str:
    return _DEVICE


def local_cuda_available() -> bool:
    """True if the host can run the vision models on a local GPU."""
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False
