"""Load a manga page into a numpy array."""
from __future__ import annotations

import numpy as np
from PIL import Image


def load_image(path: str) -> np.ndarray:
    """Load an image page as an RGB numpy array."""
    return np.array(Image.open(path).convert("RGB"))
