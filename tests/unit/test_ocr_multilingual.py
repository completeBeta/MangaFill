"""Unit tests for multi-language OCR routing + language detection.

`detect_language` and `_reocr_rotated` are tested with `read_boxes_text` /
`_pipeline` mocked — no PaddleOCR model load. The key regression guard: the
`source_lang=auto` hang fix means the korean recognizer is NEVER probed on a
Chinese/Japanese page (only `ch`, which reads hanzi AND kana, is run first).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

import app.pipeline.ocr_multilingual as om


def _img(w: int = 10, h: int = 10) -> Image.Image:
    return Image.new("RGB", (w, h))


def test_detect_language_chinese_probes_ch_only(monkeypatch):
    """A Chinese page classifies from the `ch` probe alone — the korean
    recognizer (whose detection floods dense CJK pages with false positives)
    is never loaded."""
    calls: list[str] = []

    def fake_read(image, lang):
        calls.append(lang)
        return [((0, 0, 30, 20), "你好世界", 0.95)] if lang == "ch" else []

    monkeypatch.setattr(om, "read_boxes_text", fake_read)
    monkeypatch.setattr(om, "_drop", lambda *a, **k: None)
    assert om.detect_language(_img()) == "zh"
    assert calls == ["ch"]


def test_detect_language_japanese_probes_ch_only(monkeypatch):
    calls: list[str] = []

    def fake_read(image, lang):
        calls.append(lang)
        return [((0, 0, 30, 20), "こんにちは", 0.95)] if lang == "ch" else []

    monkeypatch.setattr(om, "read_boxes_text", fake_read)
    monkeypatch.setattr(om, "_drop", lambda *a, **k: None)
    assert om.detect_language(_img()) == "ja"
    assert calls == ["ch"]


def test_detect_language_korean_falls_back_to_korean_probe(monkeypatch):
    """`ch` cannot read hangul, so a Korean page falls through to the korean
    recognizer (and only then — never the other way around)."""
    calls: list[str] = []

    def fake_read(image, lang):
        calls.append(lang)
        if lang == "ch":
            return []  # no CJK signal
        return [((0, 0, 30, 20), "안녕하세요", 0.9)]

    monkeypatch.setattr(om, "read_boxes_text", fake_read)
    monkeypatch.setattr(om, "_drop", lambda *a, **k: None)
    assert om.detect_language(_img()) == "ko"
    assert calls == ["ch", "ko"]


def test_detect_language_blank_defaults_to_japanese(monkeypatch):
    calls: list[str] = []

    def fake_read(image, lang):
        calls.append(lang)
        return []

    monkeypatch.setattr(om, "read_boxes_text", fake_read)
    monkeypatch.setattr(om, "_drop", lambda *a, **k: None)
    assert om.detect_language(_img()) == "ja"
    assert calls == ["ch", "ko"]


def test_detect_language_low_confidence_hanzi_not_chinese(monkeypatch):
    """Hanzi read below the 0.4 confidence floor is not treated as Chinese."""
    def fake_read(image, lang):
        if lang == "ch":
            return [((0, 0, 30, 20), "你好", 0.3)]
        return []

    monkeypatch.setattr(om, "read_boxes_text", fake_read)
    monkeypatch.setattr(om, "_drop", lambda *a, **k: None)
    assert om.detect_language(_img()) == "ja"


def test_reocr_rotated_upgrades_low_conf_vertical_read(monkeypatch):
    """A weakly-read vertical column is re-read after a 90° rotation; the
    higher-confidence rotated read wins, mapped back to the original box."""
    class _FakePipeline:
        def predict(self, _arr):
            return [{
                "dt_polys": [[[0, 0], [0, 10], [20, 10], [20, 0]]],
                "rec_texts": ["HELLO"],
                "rec_scores": [0.9],
            }]

    monkeypatch.setattr(om, "_pipeline", lambda lang: _FakePipeline())
    arr = np.zeros((50, 50, 3), dtype=np.uint8)
    text, conf = om._reocr_rotated(arr, 10, 10, 20, 40, "garbage", 0.2, "zh")
    assert text == "HELLO"
    assert conf == 0.9


def test_reocr_rotated_keeps_original_when_rotated_read_is_worse(monkeypatch):
    class _FakePipeline:
        def predict(self, _arr):
            return [{
                "dt_polys": [[[0, 0], [0, 10], [20, 10], [20, 0]]],
                "rec_texts": ["junk"],
                "rec_scores": [0.1],
            }]

    monkeypatch.setattr(om, "_pipeline", lambda lang: _FakePipeline())
    arr = np.zeros((50, 50, 3), dtype=np.uint8)
    text, conf = om._reocr_rotated(arr, 10, 10, 20, 40, "orig", 0.2, "zh")
    assert (text, conf) == ("orig", 0.2)  # 0.1 <= 0.2, so the original stays


def test_reocr_rotated_skips_when_crop_too_small(monkeypatch):
    # A tiny image makes the padded crop smaller than the 8px floor -> no rotate.
    monkeypatch.setattr(om, "_pipeline", lambda lang: None)
    arr = np.zeros((5, 5, 3), dtype=np.uint8)
    text, conf = om._reocr_rotated(arr, 0, 0, 3, 4, "orig", 0.1, "zh")
    assert (text, conf) == ("orig", 0.1)
