"""End-to-end page render: detect → OCR → translate → inpaint → typeset."""
from __future__ import annotations

from PIL import Image

from .inpaint import inpaint_text
from .ingest import load_image
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

    # Erase JP text: vertical (translated) + furigana (ruby). Titles/watermarks stay.
    erase = [b.bbox for b in blocks if b.orientation in ("vertical", "furigana")]
    image = Image.fromarray(load_image(image_path))
    inpainted = inpaint_text(image, erase) if erase else image

    result = typeset_page(inpainted, blocks)
    return result, blocks, cost
