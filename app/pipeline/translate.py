"""Translation — LLM (OpenRouter, cloud-only, no local GPU).

Batches a page's translatable lines (vertical dialogue) into one request, with
the page's reading order preserved, so the model keeps speaker/tone consistency.
Uses a NUMBERED response format (anchored per line) so an LLM preamble can't
shift alignment. Returns (translations, cost_usd); mutates `translation` field.
"""
from __future__ import annotations

import re
import unicodedata

import httpx

from .types import TextBlock

SYSTEM_PROMPT = (
    "You are a professional manga translator. Translate each numbered Japanese "
    "line to natural, concise English that fits a speech bubble. Preserve tone "
    "(casual/formal/angry) and speaker consistency. Sound effects (onomatopoeia): "
    "give a brief English equivalent or transliteration (e.g. おえっぷ -> 'Gagh'), "
    "not dialogue. Output ONLY numbered lines in the exact format 'N. <translation>', "
    "one per line. No preamble, no explanations, no extra text."
)


def _parse_numbered(content: str, n: int) -> list[str]:
    """Extract 'N. <translation>' lines; ignore any preamble/extra text."""
    found: dict[int, str] = {}
    for m in re.finditer(r"^\s*(\d+)\s*[.)、:：]\s*(.+?)\s*$", content, re.MULTILINE):
        num = int(m.group(1))
        if 1 <= num <= n:
            found[num] = m.group(2).strip().strip('"\'“”')
    return [found.get(i + 1, "") for i in range(n)]


_CJK_RANGES = (
    ("\u3040", "\u30ff"),  # hiragana + katakana
    ("\u4e00", "\u9fff"),  # kanji
    ("\u3000", "\u303f"),  # CJK punctuation (。「」…)
)

# Markers small translation models emit when they can't handle a line instead of
# (or in addition to) returning English. Case-insensitive substring match.
_PLACEHOLDER_MARKERS = (
    "[text untranslatable]",
    "[untranslatable]",
    "[untranslated]",
    "untranslatable",
    "[no translation]",
    "[unable to translate]",
    "[cannot be translated]",
    # bare / hyphenated refusal forms (the model can't read garbled OCR):
    "unable to translate",
    "cannot translate",
    "garbled text",
    "meaningless",
)


def _has_cjk(text: str) -> bool:
    """True if `text` contains any kana/kanji/CJK punctuation."""
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES for ch in text)


# Typographic/Unicode characters small models emit that the comic fonts (Anime
# Ace, etc.) don't carry a glyph for — rendered as an empty box ("tofu").
_PUNCT_TO_ASCII = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',   # curly double quotes
    "\u2013": "-", "\u2014": "-", "\u2015": "-",   # en/em/horizontal-bar dashes
    "\u2212": "-",    # minus sign
    "\u2026": "...",  # ellipsis
    "\u2022": "-",    # bullet
    "\u00b7": "-",    # middle dot
    "\u00a0": " ",    # non-breaking space
    "\u2009": " ", "\u200a": " ",   # thin spaces
    "\u3000": " ",    # ideographic space
}


def _normalize_ascii(s: str) -> str:
    """Map a translation to plain ASCII so every glyph the typesetter draws has
    a font glyph (no empty box / tofu). Decomposes accented Latin (é -> e), maps
    typographic punctuation to ASCII, and drops anything still non-ASCII (leaked
    kana/kanji, stray symbols)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = "".join(_PUNCT_TO_ASCII.get(ch, ch) for ch in s)
    s = "".join(ch for ch in s if ord(ch) < 128)
    return s.strip()


def _clean_translation(raw: str) -> str:
    """Return a usable ASCII English translation, or "" if the line is skipped.

    Rejects placeholder/refusal markers and text that is still Japanese (the
    model echoing the source back). Normalizes to ASCII so a translation that is
    mostly English but carries a leaked kanji/kana or smart punctuation is
    cleaned to renderable glyphs rather than dropped wholesale or drawn as tofu.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if any(m in low for m in _PLACEHOLDER_MARKERS):
        return ""
    ascii_s = _normalize_ascii(s)
    if not ascii_s:
        return ""  # entirely non-ASCII — the model echoed Japanese instead
    return ascii_s


def translate_lines(
    lines: list[str],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
) -> tuple[list[str], int, int]:
    """Translate a batch of JP lines to EN.

    Returns (translations, prompt_tokens, completion_tokens) — the token counts
    come from the API `usage` object so the caller can price the call.
    """
    if not lines:
        return [], 0, 0

    user = "Translate these manga lines:\n" + "\n".join(
        f"{i + 1}. {t}" for i, t in enumerate(lines)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": 4000,
        "temperature": 0.2,
    }
    # DeepSeek v4 models reason by default, burning the max_tokens budget and
    # returning empty content on larger batches. Disable reasoning when the
    # endpoint is DeepSeek (harmless no-op on other OpenAI-compatible providers).
    if "deepseek" in base_url.lower():
        payload["thinking"] = {"type": "disabled"}

    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage", {}) or {}
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0

    translations = _parse_numbered(content, len(lines))
    return translations, prompt_tokens, completion_tokens


def translate_page(
    blocks: list[TextBlock],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    translate_horizontal: bool = False,
) -> tuple[list[TextBlock], int, int]:
    """Translate the translatable blocks of a page.

    Vertical dialogue is always translated. Horizontal text (stat lines, names,
    titles) is translated only when ``translate_horizontal`` is set — the caller
    sets it on pages that have vertical content (stat/character pages), and
    leaves it off for pure-horizontal pages (covers/credits) whose text should
    stay as-is. Furigana is never translated.

    LLM output is sanitized before it is written to a block: placeholder/refusal
    markers (e.g. a literal "[TEXT UNTRANSLATABLE]"), empty lines, and text that
    still contains kana/kanji (the model echoing the source back instead of
    translating) are all treated as "no translation". The block then keeps an
    empty `translation`, so the typesetter leaves the original Japanese intact
    rather than painting garbage onto the page.
    """
    translatable = [
        b for b in blocks
        if b.text and (b.orientation == "vertical"
                       or (translate_horizontal and b.orientation == "horizontal"))
    ]
    if not translatable:
        return blocks, 0, 0

    translations, pt, ct = translate_lines(
        [b.text for b in translatable], model, api_key, base_url
    )
    for b, en in zip(translatable, translations):
        b.translation = _clean_translation(en)

    # Retry any line that came back empty despite having source text. A batched
    # request occasionally drops a line (truncation near max_tokens, or numbering
    # drift), which would otherwise silently leave that bubble untranslated.
    for b in translatable:
        if b.translation or not b.text:
            continue
        try:
            (en,), p2, c2 = translate_lines([b.text], model, api_key, base_url)
            b.translation = _clean_translation(en)
            pt += p2
            ct += c2
        except Exception:
            pass  # leave untranslated (original kept) rather than crash the page

    return blocks, pt, ct
