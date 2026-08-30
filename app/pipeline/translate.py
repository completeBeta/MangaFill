"""Translation — LLM (OpenRouter, cloud-only, no local GPU).

Batches a page's translatable lines (vertical dialogue) into one request, with
the page's reading order preserved, so the model keeps speaker/tone consistency.
Uses a NUMBERED response format (anchored per line) so an LLM preamble can't
shift alignment. Returns (translations, cost_usd); mutates `translation` field.
"""
from __future__ import annotations

import re

import httpx

from .types import TextBlock

SYSTEM_PROMPT = (
    "You are a professional manga translator. Translate each numbered Japanese "
    "line to natural, concise English that fits a speech bubble. Preserve tone "
    "(casual/formal/angry) and speaker consistency. Output ONLY numbered lines "
    "in the exact format 'N. <translation>', one per line. No preamble, no "
    "explanations, no extra text."
)


def _parse_numbered(content: str, n: int) -> list[str]:
    """Extract 'N. <translation>' lines; ignore any preamble/extra text."""
    found: dict[int, str] = {}
    for m in re.finditer(r"^\s*(\d+)\s*[.)、:：]\s*(.+?)\s*$", content, re.MULTILINE):
        num = int(m.group(1))
        if 1 <= num <= n:
            found[num] = m.group(2).strip().strip('"\'“”')
    return [found.get(i + 1, "") for i in range(n)]


def translate_lines(
    lines: list[str],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
) -> tuple[list[str], float]:
    """Translate a batch of JP lines to EN. Returns (translations, cost_usd)."""
    if not lines:
        return [], 0.0

    user = "Translate these manga lines:\n" + "\n".join(
        f"{i + 1}. {t}" for i, t in enumerate(lines)
    )
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "max_tokens": 4000,
            "temperature": 0.2,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    cost = data.get("usage", {}).get("cost", 0.0)

    translations = _parse_numbered(content, len(lines))
    return translations, cost


def translate_page(
    blocks: list[TextBlock],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
) -> tuple[list[TextBlock], float]:
    """Translate the translatable blocks (vertical dialogue) of a page.

    Furigana and horizontal text (titles/watermarks) are intentionally skipped.
    """
    translatable = [b for b in blocks if b.orientation == "vertical" and b.text]
    if not translatable:
        return blocks, 0.0

    translations, cost = translate_lines(
        [b.text for b in translatable], model, api_key, base_url
    )
    for b, en in zip(translatable, translations):
        b.translation = en
    return blocks, cost
