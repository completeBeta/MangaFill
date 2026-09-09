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


def test_is_noise_box_drops_small_low_conf_keeps_small_high_conf():
    # Foliage reads as single hanzi at LOW-moderate confidence -> noise.
    assert om.is_noise_box(39, 42, 0.707) is True   # 业 leaf
    assert om.is_noise_box(50, 51, 0.860) is True   # 义 leaf
    assert om.is_noise_box(25, 28, 0.386) is True   # 水 texture
    # Real small text reads HIGH confidence -> keep (this was the regression).
    assert om.is_noise_box(49, 49, 0.949) is False  # 嗝 SFX
    assert om.is_noise_box(49, 52, 1.000) is False  # lone 这
    # Anything with a dimension >= floor is never noise, regardless of conf.
    assert om.is_noise_box(63, 58, 0.5) is False    # 啊 SFX
    assert om.is_noise_box(160, 146, 0.2) is False  # 啪 SFX
    assert om.is_noise_box(220, 127, 0.1) is False  # 主人 dialogue
    # Unknown confidence -> fail open (keep).
    assert om.is_noise_box(30, 30, None) is False


def test_read_boxes_text_drops_noise_boxes(monkeypatch):
    # A foliage-sized LOW-confidence detection must never reach block-building,
    # but a small HIGH-confidence one (real SFX) must survive.
    class _FakePipeline:
        def predict(self, _arr):
            return [{
                "dt_polys": [
                    [[0, 0], [0, 200], [60, 200], [60, 0]],     # real vertical text
                    [[900, 900], [900, 940], [940, 940], [940, 900]],  # leaf noise (low conf)
                    [[500, 500], [500, 549], [549, 549], [549, 500]],  # 嗝 SFX (high conf)
                ],
                "rec_texts": ["问世间情为何物", "义", "嗝"],
                "rec_scores": [0.99, 0.86, 0.95],
            }]

    monkeypatch.setattr(om, "_pipeline", lambda lang: _FakePipeline())
    monkeypatch.setattr(om, "_reocr_rotated", lambda *a, **k: ("x", 0.5))
    img = Image.new("RGB", (1000, 1000))
    boxes = om.read_boxes_text(img, "zh")
    assert [t for _b, t, _c in boxes] == ["问世间情为何物", "嗝"]
