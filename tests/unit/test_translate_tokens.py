"""translate_lines captures prompt/completion tokens from the API usage object."""
from __future__ import annotations

import httpx

import app.pipeline.translate as translate


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "choices": [{"message": {"content": "1. Hello\n2. World\n"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }


def test_translate_lines_returns_tokens(monkeypatch):
    def fake_post(*a, **k):
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    translations, pt, ct = translate.translate_lines(
        ["こんにちは", "世界"], "test-model", "sk-x", "https://example.test/v1"
    )
    assert translations == ["Hello", "World"]
    assert pt == 120
    assert ct == 30


def test_clean_translation_rejects_placeholders():
    assert translate._clean_translation("[TEXT UNTRANSLATABLE]") == ""
    assert translate._clean_translation("  [untranslatable]  ") == ""
    assert translate._clean_translation("[no translation]") == ""
    assert translate._clean_translation("untranslatable") == ""


def test_clean_translation_rejects_pure_japanese():
    assert translate._clean_translation("お疲れ様でした クズノハ様") == ""
    assert translate._clean_translation("こんにちは") == ""


def test_clean_translation_keeps_english_and_strips_stray_cjk():
    assert translate._clean_translation("Hello, world") == "Hello, world"
    # A mostly-English line carrying a leaked kanji keeps its Latin content.
    assert translate._clean_translation("STRENGTH: B+ 力") == "STRENGTH: B+"


def test_translate_page_sanitizes_garbage(monkeypatch):
    class _Fake:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "1. [TEXT UNTRANSLATABLE]\n"
                                "2. お疲れ様でした\n"
                                "3. Welcome!\n"
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Fake())
    b1 = translate.TextBlock(bbox=(0, 0, 10, 30), text="あ", orientation="vertical")
    b2 = translate.TextBlock(bbox=(0, 0, 10, 30), text="い", orientation="vertical")
    b3 = translate.TextBlock(bbox=(0, 0, 10, 30), text="う", orientation="vertical")
    blocks = [b1, b2, b3]
    out, _pt, _ct = translate.translate_page(
        blocks, "m", "k", "https://example.test/v1"
    )
    assert out[0].translation == ""   # placeholder dropped
    assert out[1].translation == ""   # Japanese echo dropped
    assert out[2].translation == "Welcome!"
