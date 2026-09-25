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


# ---------------------------------------------------------------------------------------
# THE TRANSLATION PROMPT IS UPSTREAM'S, ADAPTED — NOT OURS (2026-09-25)
#
# WHY. In engine mode every line the engine reads is translated BY THE ENGINE, using
# upstream's own prompt. The only lines that reach THIS module in engine mode are the ones
# the SECOND PASS recovered — i.e. exactly the degraded reads: a line clipped by a panel
# edge, a tail mangled by OCR. Our old prompt turned the clipped Korean fragment
# `1이르다리고이느` into "One, get up and move!": fluent English asserting what the source
# cannot support, and it displaced the engine's own correct read of that same plate.
#
# The mechanism that prevents that is upstream's, and they already ship it — one clause in
# their user prompt and two rules in their method:
#   * "If it's already in {lang} or looks like gibberish you have to output it as it is
#      instead"  (their _PROMPT_TEMPLATE)
#   * "Leave ambiguous elements as they are without interpretation"
#   * "do not add pronouns that do not exist in the original text"   (their method, step 3)
# So we use their prompt. Measured before adopting (2026-09-25, deepseek-v4-flash, the real
# page's own reads): on the clipped fragment their prompt returns the source unchanged where
# ours invented a sentence, and on the readable plates both agree ("Hankyung Department
# Store" / "South Korea sales scale"). A second benefit that matters on a page: recovered
# lines now read in the SAME voice as the lines the engine translated itself.
#
# SOURCE: manga_translator/translators/config_gpt.py @ commit 95227a2b
# (_CHAT_SYSTEM_TEMPLATE, _PROMPT_TEMPLATE). Adapted in two ways and nothing else: their
# '<|N|>' prefix becomes our 'N.' (our parser is anchored to it), and our two render-side
# additions below. No markers, no abstention rules, no anti-invention scaffolding of ours.
_UPSTREAM_METHOD = (
    "Ignore all preceding instructions. Follow only what is defined below.\n"
    "## Role: Professional Comic Translator\n"
    "You are an expert translation engine that specialises in manga, manhwa, manhua and "
    "comic content for all languages.\n"
    "## Translation Method\n"
    "1. LITERAL TRANSLATION:\n"
    "- Provide a precise word-for-word translation of each textline.\n"
    "- Maintain the original sentence structure where possible.\n"
    "- Preserve all original markers and expressions.\n"
    "- Leave ambiguous elements as they are without interpretation.\n"
    "2. ANALYSIS & DE-VERBALIZATION:\n"
    "- Capture the core meaning, emotional tone and cultural nuances.\n"
    "- Identify logical connections between fragmented text segments.\n"
    "- Analyse the shortcomings and areas for improvement of the literal translation.\n"
    "3. REFINEMENT:\n"
    "- Adjust the translation to sound natural in {lang} while maintaining the original "
    "meaning.\n"
    "- Preserve the emotional tone and intensity appropriate to manga and otaku culture.\n"
    "- Ensure consistency in character voice and terminology.\n"
    "- Determine appropriate pronouns from context; do not add pronouns that do not exist "
    "in the original text.\n"
    "- Refine based on the conclusions from the second step.\n"
    "## Translation Rules\n"
    "- Translate line by line, faithfully reproducing the original text and its emotional "
    "intent.\n"
    "- Preserve original gibberish and sound effects without translation.\n"
    "- Output each line as 'N. <translation>' — the translation only, never the raw source "
    "text.\n"
    "- Translate content only: no additional interpretation or commentary.\n"
)

# The two things upstream's prompt does not know about: our typesetter draws INSIDE a
# balloon (so the line has to be short), and a character's name must not run into their
# speech (a name collision drew "Meng Erfei-you've hit me…" once; this rule is the fix).
_FIT_AND_NAMES = (
    "## Fitting the bubble\n"
    "- Keep each line concise enough to sit inside a comic speech bubble — the shortest "
    "phrasing that keeps the meaning.\n"
    "- Keep a character's name separate from their spoken line with a comma and space "
    "(e.g. \"Meng Erfei, you've hit me…\", never \"Meng Erfei-you've…\").\n"
)

# Korean manhwa/webtoon dialogue carries register markers the generic prompt does not name:
# banmal (casual) vs jondaenmal (honorific). A literal rendering flattens them into stiff
# English, so they are called out explicitly.
_KO_REGISTER = (
    "## Korean register\n"
    "- Render banmal (casual/informal speech) as relaxed, everyday English and jondaenmal "
    "(formal/honorific speech) as more polite or formal English; keep slang, playful "
    "banter and short emotional outbursts natural and idiomatic.\n"
)

# Which language the lines ARE. Upstream's template never names the source (their pipeline
# knows it from OCR); ours tells the model, because in this app the same prompt serves ja,
# ko and zh.
_SOURCE_NOTE = "## Source\n- The text to translate is {src}.\n"

SYSTEM_PROMPT = _UPSTREAM_METHOD + _FIT_AND_NAMES + _SOURCE_NOTE
SYSTEM_PROMPT_KO = _UPSTREAM_METHOD + _FIT_AND_NAMES + _SOURCE_NOTE + _KO_REGISTER

# Upstream's user-prompt line, verbatim apart from naming our numbered format instead of
# their prefix — INCLUDING the rule that does the real work in this app: text that is not
# readable comes back AS IT IS rather than as a plausible guess. `{lang}` is the TARGET
# language, never the source (see `_system_prompt`).
_PROMPT_TEMPLATE = (
    "Please help me to translate the following text from a manga to {lang}. "
    "If it's already in {lang} or looks like gibberish you have to output it as it is "
    "instead. Keep the 'N.' numbering format."
)

# The TARGET of this module's prompt. Our own path always renders English (the engine is
# asked for the same by `translator.target_lang: ENG`, and `_clean_translation` drops
# anything that is not renderable ASCII), so upstream's `{to_lang}` slots get English.
TARGET_LANG = "English"

# UPSTREAM'S FEW-SHOT SAMPLE — their request carries it, so ours does too.
# `common_gpt._assemble_request` puts a target-language example in the conversation BEFORE the
# real prompt (user: source lines, assistant: the English they want). We keep it for fidelity
# with the engine's own translations — a page is lettered from the engine's output AND from
# ours, so both should be asked the same way. Verbatim their `_CHAT_SAMPLE['English']` in our
# 'N.' numbering; their sample is keyed by TARGET language, not by source, and they use the
# same Japanese lines whatever the page's language is.
#
# MEASURED (2026-09-25): with the sample and without it, this app's own path translates clean
# ja/ko/zh dialogue identically (0 lines handed back as the source either way — fewshot_probe.py,
# 20-line batches). The "30% of clean Chinese lines echoed" that first pointed here was an
# artifact of a MEASUREMENT SCRIPT that assembled its own messages without the sample; it was
# never the app's behaviour. Kept because it is upstream's shape and costs ~60 tokens.
_SAMPLE_IN = ("1. 恥ずかしい… 目立ちたくない… 私が消えたい…\n"
              "2. きみ… 大丈夫⁉\n"
              "3. なんだこいつ 空気読めて ないのか…？")
_SAMPLE_OUT = ("1. I'm embarrassed... I don't want to stand out... I want to disappear...\n"
               "2. Are you okay?\n"
               "3. What's wrong with this guy? Can't he read the situation...?")


def _system_prompt(source_lang: str) -> str:
    """The system prompt for a page whose text is `source_lang` and whose output is English.

    The two languages are SEPARATE parameters in upstream's template, and mixing them is a
    real defect, not a cosmetic one — measured 2026-09-25 on the ko corpus: with the source
    language formatted into `{to_lang}`, upstream's own rule ("if it's already in {to_lang}
    or looks like gibberish you have to output it as it is") told the model the Korean input
    was already the target, so it echoed clean Korean straight back: clean-source SUPPORTED
    collapsed 93.1% -> 57.4% while 50% of lines came back untranslated. `{to_lang}` is
    English here; the source is named separately, in `## Source`.
    """
    src = LANG_NAMES.get(source_lang, "Japanese")
    tmpl = SYSTEM_PROMPT_KO if source_lang == "ko" else SYSTEM_PROMPT
    return tmpl.format(lang=TARGET_LANG, src=src)


def _messages(source_lang: str, lines: list[str]) -> list[dict]:
    """Upstream's conversation shape: system, the target-language example, then the work."""
    user = _PROMPT_TEMPLATE.format(lang=TARGET_LANG) + "\n" + "\n".join(
        f"{i + 1}. {t}" for i, t in enumerate(lines)
    )
    return [
        {"role": "system", "content": _system_prompt(source_lang)},
        {"role": "user", "content": _SAMPLE_IN},
        {"role": "assistant", "content": _SAMPLE_OUT},
        {"role": "user", "content": user},
    ]


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
        return ""  # entirely non-ASCII — the model echoed the source instead
    return ascii_s


def _echoed_source(raw: str) -> bool:
    """True when the model handed the source back — upstream's rule for unreadable text.

    Upstream's prompt says unreadable text must be "output as it is instead", so a line that
    comes back with no English in it is a DELIBERATE answer, not a dropped one. The block then
    keeps an empty translation (the typesetter leaves the original artwork alone) and the line
    is not chased with a retry — the source is what it is.

    "No English" has to be judged on letters, not on `_normalize_ascii` alone: a degraded OCR
    read like `1이르다리고이느` carries a stray ASCII digit, so ASCII-stripping leaves "1" and a
    lone digit would otherwise be lettered into the bubble as if it were a translation. So:
    the reply carries non-ASCII (the model gave something back in the source script) AND
    nothing in it is an ASCII letter.
    """
    s = (raw or "").strip()
    if not s or not any(ord(c) > 127 for c in s):
        return False
    return not re.search(r"[A-Za-z]", _normalize_ascii(s))


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


def _translate_raw(
    lines: list[str],
    model: str,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    source_lang: str = "ja",
    timeout: float | None = None,
    max_retries: int | None = None,
) -> tuple[list[str], int, int]:
    """One batched request; returns the model's RAW reply per line (before cleaning).

    The raw form matters: a line that comes back as the source text is upstream's
    "output it as it is" answer for unreadable text (see `_echoed_source`), which is
    different from a line the model dropped — the caller treats them differently.
    """
    if not lines:
        return [], 0, 0

    timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
    retries = DEFAULT_MAX_RETRIES if max_retries is None else max(0, int(max_retries))

    # Upstream's user prompt, verbatim in substance: the gibberish/already-in-target rule
    # is the one that stops an unreadable line from being answered with a guess. The target
    # is English (never the source language — see `_system_prompt`), and the conversation
    # carries upstream's example first (`_messages`) — without it the model echoes ordinary
    # dialogue back instead of translating it.
    payload = {
        "model": model,
        "messages": _messages(source_lang, lines),
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

    return _parse_numbered(content, len(lines)), prompt_tokens, completion_tokens


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
    raw, prompt_tokens, completion_tokens = _translate_raw(
        lines, model, api_key, base_url, source_lang=source_lang,
        timeout=timeout, max_retries=max_retries,
    )
    return [_clean_translation(r) for r in raw], prompt_tokens, completion_tokens


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

    raws, pt, ct = _translate_raw(
        [b.text for b in translatable], model, api_key, base_url,
        source_lang=source_lang, timeout=timeout, max_retries=max_retries,
    )
    for b, raw in zip(translatable, raws):
        # An echoed answer is upstream's "output it as it is" for unreadable text: no
        # translation (the original artwork is kept), and never a stray digit or symbol
        # that survived ASCII-stripping being lettered as if it were English.
        b.translation = "" if _echoed_source(raw) else _clean_translation(raw)

    # Retry any line that came back empty despite having source text. A batched
    # request occasionally drops a line (truncation near max_tokens, or numbering
    # drift), which would otherwise silently leave that bubble untranslated.
    # NOT retried: a line the model echoed back AS THE SOURCE. That is upstream's answer
    # for unreadable text ("output it as it is instead"), so there is nothing to chase —
    # the block stays untranslated and the artwork is left alone, with no invented English.
    echoed = 0
    for b, raw in zip(translatable, raws):
        if b.translation or not b.text:
            continue
        if _echoed_source(raw):
            echoed += 1
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

    if echoed:
        log.info("translation: %d of %d lines came back as the source text "
                 "(unreadable — upstream's 'output it as it is' rule); left untranslated "
                 "and not retried", echoed, len(translatable))

    return blocks, pt, ct
