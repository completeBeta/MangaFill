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
    # bare/hyphenated refusal forms the model emits on garbled OCR
    assert translate._clean_translation("(Garbled text-unable to translate meaningfully)") == ""
    assert translate._clean_translation("cannot translate this") == ""
    assert translate._clean_translation("[Unintelligible text-likely OCR corruption]") == ""


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


def test_translate_page_retries_missing_lines(monkeypatch):
    def fake_post(*a, **k):
        user = k["json"]["messages"][1]["content"]
        n_lines = sum(1 for l in user.split("\n") if l.strip()[:1].isdigit())

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                content = "1. Hello" if n_lines > 1 else "1. World"
                return {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    b1 = translate.TextBlock(bbox=(0, 0, 10, 30), text="あ", orientation="vertical")
    b2 = translate.TextBlock(bbox=(0, 0, 10, 30), text="い", orientation="vertical")
    out, _pt, _ct = translate.translate_page(
        [b1, b2], "m", "k", "https://example.test/v1"
    )
    assert out[0].translation == "Hello"
    assert out[1].translation == "World"  # dropped in batch, recovered on retry


def test_translate_page_horizontal_only_when_requested(monkeypatch):
    class _Fake:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "1. Alpha\n2. Beta\n"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Fake())
    hv = translate.TextBlock(bbox=(0, 0, 10, 30), text="あ", orientation="vertical")
    hh = translate.TextBlock(bbox=(0, 0, 40, 12), text="い", orientation="horizontal")

    # Without the flag, horizontal text is left untranslated (cover/credit pages).
    out, _pt, _ct = translate.translate_page([hv, hh], "m", "k", "https://x/v1")
    assert out[0].translation == "Alpha"
    assert out[1].translation == ""

    # With the flag (stat/character pages), horizontal text is translated too.
    out2, _pt2, _ct2 = translate.translate_page(
        [hv, hh], "m", "k", "https://x/v1", translate_horizontal=True
    )
    assert out2[0].translation == "Alpha"
    assert out2[1].translation == "Beta"


def test_normalize_ascii_maps_smart_punctuation():
    assert translate._normalize_ascii("It\u2019s \u2014 okay\u2026") == "It's - okay..."
    assert translate._normalize_ascii("\u201cHello\u201d") == '"Hello"'


def test_normalize_ascii_strips_cjk_and_accents():
    assert translate._normalize_ascii("Pok\u00e9mon \u529b") == "Pokemon"
    assert translate._normalize_ascii("caf\u00e9") == "cafe"


def test_clean_translation_normalizes_to_ascii():
    # Smart quotes + em-dash + ellipsis map to renderable ASCII (no tofu boxes).
    assert translate._clean_translation("\u201cI\u2019m fine \u2014 really\u2026\u201d") == "\"I'm fine - really...\""
