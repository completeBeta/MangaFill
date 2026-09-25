"""Device selection for the local vision models (detect / OCR / inpaint).

The pipeline can run its vision models three ways:

  * **local CPU** — default; works everywhere.
  * **local GPU** — if the host has CUDA and a CUDA-enabled torch, the models run
    on the GPU directly (no separate worker needed). Torch presents both NVIDIA
    CUDA and AMD ROCm as the ``cuda`` device, so ``cuda`` covers both vendors.
  * **external GPU worker** — `gpu_worker_url` offloads detect+OCR/inpaint to a
    separate service (`remote.py`); handled in `render.py`, not here.

`set_device()` is called at startup from `settings.device` (``auto`` / ``cpu`` /
``cuda``) and again whenever the user changes the device in the web UI. ``auto``
uses CUDA when available and falls back to CPU otherwise — the safe default for
both CPU-only and GPU hosts.
"""
from __future__ import annotations

_DEVICE = "cpu"


def _torch():
    try:
        import torch
        return torch
    except Exception:
        return None


def cuda_available() -> bool:
    """True if the local torch has a usable CUDA device (NVIDIA CUDA or AMD ROCm)."""
    t = _torch()
    return bool(t is not None and t.cuda.is_available())


def backend() -> str:
    """Accelerator vendor of the local torch: ``cuda`` (NVIDIA), ``rocm`` (AMD —
    torch presents ROCm as the ``cuda`` device), or ``cpu``."""
    if cuda_available():
        t = _torch()
        if t is not None and getattr(t.version, "hip", None):
            return "rocm"
        return "cuda"
    return "cpu"


def resolve(configured: str) -> str:
    """Map a configured device name to a concrete torch device string."""
    c = (configured or "auto").strip().lower()
    if c == "cpu":
        return "cpu"
    if c in ("auto", "cuda", "gpu") and cuda_available():
        return "cuda"
    return "cpu"


def set_device(configured: str) -> None:
    global _DEVICE
    _DEVICE = resolve(configured)


def get_device() -> str:
    return _DEVICE


def local_cuda_available() -> bool:
    """Back-compat alias — True if the host can run the vision models on a local GPU."""
    return cuda_available()
