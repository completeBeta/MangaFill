"""End-to-end page render: detect → OCR → translate → inpaint → typeset.

Typesetting is bubble-aware: each translated block is re-lettered into its
enclosing white bubble/box (recovered by `bubble.find_container`), not the tight
vertical text column — English is horizontal and needs the bubble's width.

Free-floating text (editorial teasers / handwritten text over screentone/artwork,
which have no clean white container) is left untouched — its Japanese stays, no
inpaint, no English — rather than drawing garbage over the art.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .bubble import find_container, is_free_floating
from .ingest import load_image
from .inpaint import inpaint_text
from .pipeline import process_page
from .translate import translate_page
from .types import TextBlock
from .typeset import typeset_page


def render_translated_page(
    image_path: str,
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
) -> tuple[Image.Image, list[TextBlock], float]:
    """Run the full pipeline on one page.

    Returns (result PIL image, blocks with translations, cost_usd).
    """
    blocks = process_page(image_path)
    blocks, cost = translate_page(blocks, model, api_key, base_url)

    image = Image.fromarray(load_image(image_path))
    gray = np.asarray(image.convert("L"))

    targets: list[tuple[TextBlock, tuple]] = []
    erase: list[tuple] = []
    for b in blocks:
        if b.orientation == "furigana":
            erase.append(b.bbox)  # erase ruby, never re-letter
            continue
        if not b.translation:
            continue
        if is_free_floating(gray, b.bbox):
            continue  # free-floating over art: leave original JP untouched
        # Bubble text: erase JP, typeset into the bubble (fallback: text bbox).
        targets.append((b, find_container(gray, b.bbox)))
        erase.append(b.bbox)

    inpainted = inpaint_text(image, erase) if erase else image
    only = {id(b) for b, _region in targets}
    regions = {id(b): region for b, region in targets if region is not None}
    result = typeset_page(inpainted, blocks, regions=regions, only=only)
    return result, blocks, cost
