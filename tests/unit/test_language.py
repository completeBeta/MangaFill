"""Unit tests for source-language script helpers (pure functions — no OCR)."""
from app.pipeline.language import (
    detect_from_text,
    has_cjk_or_hangul,
    has_hangul,
    has_hanzi,
    has_kana,
)


def test_script_detectors():
    assert has_kana("こんにちは") is True          # hiragana
    assert has_kana("キャロ") is True             # katakana
    assert has_kana("你好") is False              # hanzi only
    assert has_hangul("안녕하세요") is True
    assert has_hangul("こんにちは") is False
    assert has_hanzi("你好世界") is True
    assert has_hanzi("漢字") is True              # kanji shares the ideograph range
    assert has_hanzi("안녕") is False


def test_has_cjk_or_hangul_covers_all_three_scripts():
    assert has_cjk_or_hangul("こんにちは") is True   # JP
    assert has_cjk_or_hangul("안녕하세요") is True   # KO
    assert has_cjk_or_hangul("你好") is True        # ZH
    assert has_cjk_or_hangul("Hello world") is False  # already-English


def test_detect_from_text():
    assert detect_from_text("こんにちは") == "ja"
    assert detect_from_text("キャロです") == "ja"
    assert detect_from_text("안녕하세요") == "ko"
    assert detect_from_text("你好，世界") == "zh"
    assert detect_from_text("Hello world") == "en"
