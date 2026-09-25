"""Source-language helpers (Japanese / Korean / Chinese) + script detection.

The OCR models are per-language, so the pipeline needs to know which language a
page is written in before it can OCR it. That's a chicken-and-egg, broken by a
cheap script heuristic: run a recognition model, then classify the returned
characters by Unicode block. The three scripts are unambiguous:

  * kana (hiragana/katakana) -> Japanese
  * hangul                 -> Korean
  * hanzi (CJK ideographs) -> Chinese  (Japanese kanji also lives here, but
                                         real Japanese text always carries kana)
"""
from __future__ import annotations

_HIRAGANA = (0x3040, 0x309F)
_KATAKANA = (0x30A0, 0x30FF)
_HANGUL = (0xAC00, 0xD7AF)
_HANZI = (0x4E00, 0x9FFF)

# Languages the pipeline can translate FROM. "en" marks an already-English page.
SUPPORTED_LANGS = ("ja", "ko", "zh")

LANG_NAMES = {"ja": "Japanese", "ko": "Korean", "zh": "Chinese"}


def _count(text: str, lo: int, hi: int) -> int:
    return sum(1 for ch in text if lo <= ord(ch) <= hi)


def has_kana(text: str) -> bool:
    return _count(text, *_HIRAGANA) + _count(text, *_KATAKANA) > 0


def has_hangul(text: str) -> bool:
    return _count(text, *_HANGUL) > 0


def has_hanzi(text: str) -> bool:
    return _count(text, *_HANZI) > 0


def has_cjk_or_hangul(text: str) -> bool:
    """True if `text` carries any JP/KO/ZH script (or CJK punctuation)."""
    return has_kana(text) or has_hangul(text) or has_hanzi(text) or any(
        ("\u3000" <= ch <= "\u303f") for ch in text
    )


def detect_from_text(text: str) -> str:
    """Classify a chunk of OCR'd text as ja/ko/zh (or 'en' if no CJK/hangul)."""
    if has_kana(text):
        return "ja"
    if has_hangul(text):
        return "ko"
    if has_hanzi(text):
        return "zh"
    return "en"
