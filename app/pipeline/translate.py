"""Translation — LLM (cloud-only, model-agnostic OpenAI-compatible endpoint).

Batches a page's translatable lines into one request, with the page's reading
order preserved, so the model keeps speaker/tone consistency. Uses a NUMBERED
response format (anchored per line) so an LLM preamble can't shift alignment.

Provider failures are typed: `_chat` raises `ProviderError` for HTTP errors,
timeouts, connection failures, and malformed/empty responses (a 200 whose body
carries no `choices` is the classic "provider is degraded" signature — it used to
surface as a bare `KeyError: 'choices'`). Transient errors are retried with
backoff; when retries are exhausted the page fails loudly instead of silently
lettering nothing.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata

import httpx

from .language import LANG_NAMES
from .types import TextBlock

log = logging.getLogger("mangafill.translate")

# Lax defaults: a degraded provider can take minutes to answer, and killing a
# slow-but-alive call costs a whole page. Both are user-tunable in Settings
# (`llm_timeout`, `llm_max_retries`).
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_RETRIES = 2
_RETRY_BACKOFF_CAP = 20.0


class ProviderError(RuntimeError):
    """The translation provider failed (HTTP error, timeout, or bad response).

    `transient` marks errors worth retrying (timeouts, connection resets, 429,
    5xx, empty completion). `status` is the HTTP status when there was one.
    """

    def __init__(self, message: str, *, transient: bool = True, status: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.status = status


def _provider_host(base_url: str) -> str:
    """Short label for the endpoint, for user-facing error messages."""
    return (base_url or "provider").split("//")[-1].split("/")[0] or "provider"


SYSTEM_PROMPT = (
    "You are a professional comic translator. Translate each numbered {lang} "
    "line to natural, concise English that fits a speech bubble. Preserve tone "
    "(casual/formal/angry) and speaker consistency. Keep a character's name "
    "separate from their spoken line with a comma and space (e.g. \"Meng Erfei, "
    "you've hit me…\", never \"Meng Erfei-you've…\"). Sound effects (onomatopoeia): "
    "give a brief English equivalent or transliteration, "
    "not dialogue. Output ONLY numbered lines in the exact format 'N. <translation>', "
    "one per line. No preamble, no explanations, no extra text."
)

# Korean manhwa/webtoon dialogue has register markers the generic (Japanese-
# shaped) prompt doesn't capture: banmal (casual/informal speech) vs jondaenmal
# (formal/honorific speech), plus slang, playful banter, and short emotional
# outbursts. A literal rendering flattens these into stiff English, so the
# prompt calls them out explicitly and asks for natural spoken English.
SYSTEM_PROMPT_KO = (
    "You are a professional manhwa (Korean webtoon) translator. Translate each "
    "numbered Korean line to natural, concise spoken English that fits a speech "
    "bubble. Preserve the register: render banmal (casual/informal speech) as "
    "relaxed, everyday English and jondaenmal (formal/honorific speech) as more "
    "polite or formal English; keep slang, playful banter, and short emotional "
    "outbursts natural and idiomatic, and keep speaker consistency across lines. "
    "Keep a character's name separate from their spoken line with a comma and "
    "space (e.g. \"Min-ji, you've hit me…\", never \"Min-ji-you've…\"). "
    "Sound effects (onomatopoeia): give a brief English equivalent or "
    "transliteration, not dialogue. Output ONLY numbered lines in the exact "
    "format 'N. <translation>', one per line. No preamble, no explanations, no "
    "extra text."
)


def _system_prompt(source_lang: str) -> str:
    """Build the translation system prompt for the source language."""
    if source_lang == "ko":
        return SYSTEM_PROMPT_KO
    return SYSTEM_PROMPT.format(lang=LANG_NAMES.get(source_lang, "Japanese"))


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
    "unintelligible",
    "illegible",
    "gibberish",
    "ocr corruption",
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
    # The CJK ideographic ellipsis "……" maps to "......" via the table above;
    # collapse any run of 3+ dots back to a single "..." so translations don't
    # carry a literal six-dot ellipsis ("...AWAY. ......").
    s = re.sub(r"\.{3,}", "...", s)
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


def _chat(payload: dict, api_key: str, base_url: str,
          timeout: float) -> tuple[str, int, int]:
    """POST one chat completion; return (content, prompt_tokens, completion_tokens).

    Every failure mode is translated into a `ProviderError` naming the endpoint,
    so a provider outage reads as "api.deepseek.com returned no completion …"
    rather than `KeyError: 'choices'`.
    """
    host = _provider_host(base_url)
    url = f"{base_url}/chat/completions"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except httpx.TimeoutException as e:
        raise ProviderError(
            f"{host} timed out after {timeout:.0f}s (no response) — provider slow or unreachable",
            transient=True,
        ) from e
    except httpx.HTTPError as e:
        raise ProviderError(
            f"{host} connection failed ({type(e).__name__}: {e})", transient=True
        ) from e

    if resp.status_code >= 400:
        body = " ".join((resp.text or "").split())[:300]
        transient = resp.status_code == 429 or resp.status_code >= 500
        raise ProviderError(
            f"{host} returned HTTP {resp.status_code}{(' — ' + body) if body else ''}",
            transient=transient, status=resp.status_code,
        )

    try:
        data = resp.json()
    except Exception as e:
        body = " ".join((resp.text or "").split())[:200]
        raise ProviderError(
            f"{host} returned a non-JSON response ({body!r})", transient=True
        ) from e

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices or choices[0] is None:
        detail = ""
        if isinstance(data, dict) and data.get("error"):
            detail = " — " + " ".join(str(data["error"]).split())[:200]
        raise ProviderError(
            f"{host} returned no completion for model '{payload.get('model')}' "
            f"(empty/absent 'choices'){detail}",
            transient=True,
        )

    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    usage = data.get("usage") or {}
    return (content,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0))


def translate_lines(
    lines: list[str],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    source_lang: str = "ja",
    timeout: float | None = None,
    max_retries: int | None = None,
) -> tuple[list[str], int, int]:
    """Translate a batch of lines to EN, retrying transient provider failures.

    Returns (translations, prompt_tokens, completion_tokens) — the token counts
    come from the API `usage` object so the caller can price the call.

    Raises `ProviderError` once retries are exhausted (or immediately for a
    permanent error such as HTTP 401/400) — the caller surfaces it to the job so
    an outage is visible instead of silently leaving pages untranslated.
    """
    if not lines:
        return [], 0, 0

    timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
    retries = DEFAULT_MAX_RETRIES if max_retries is None else max(0, int(max_retries))

    user = "Translate these manga lines:\n" + "\n".join(
        f"{i + 1}. {t}" for i, t in enumerate(lines)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(source_lang)},
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

    host = _provider_host(base_url)
    last: ProviderError | None = None
    for attempt in range(retries + 1):
        try:
            content, prompt_tokens, completion_tokens = _chat(payload, api_key, base_url, timeout)
            break
        except ProviderError as e:
            last = e
            if not e.transient or attempt >= retries:
                log.error("translation provider failure (%s/%s attempts): %s",
                          attempt + 1, retries + 1, e)
                raise
            delay = min(_RETRY_BACKOFF_CAP, 2.0 * (2 ** attempt))
            log.warning("translation provider error (attempt %d/%d): %s — retrying in %.0fs",
                        attempt + 1, retries + 1, e, delay)
            time.sleep(delay)
    else:  # pragma: no cover - the loop always breaks or raises
        raise last if last else ProviderError(f"{host} returned no completion", transient=True)

    translations = _parse_numbered(content, len(lines))
    return translations, prompt_tokens, completion_tokens


def translate_page(
    blocks: list[TextBlock],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    translate_horizontal: bool = False,
    source_lang: str = "ja",
    timeout: float | None = None,
    max_retries: int | None = None,
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

    A `ProviderError` from the provider is NOT swallowed — it propagates so the
    job can report the outage (and stop) instead of quietly producing empty pages.
    """
    translatable = [
        b for b in blocks
        if b.text and (b.orientation == "vertical"
                       or (translate_horizontal and b.orientation == "horizontal"))
    ]
    if not translatable:
        return blocks, 0, 0

    translations, pt, ct = translate_lines(
        [b.text for b in translatable], model, api_key, base_url,
        source_lang=source_lang, timeout=timeout, max_retries=max_retries,
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
            (en,), p2, c2 = translate_lines(
                [b.text], model, api_key, base_url, source_lang=source_lang,
                timeout=timeout, max_retries=max_retries,
            )
            b.translation = _clean_translation(en)
            pt += p2
            ct += c2
        except ProviderError:
            raise  # provider is down — surface it, don't silently skip lines
        except Exception:
            pass  # leave untranslated (original kept) rather than crash the page

    return blocks, pt, ct
