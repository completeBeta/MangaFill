"""The translation prompt is upstream's, and unreadable text is never answered with a guess.

WHAT THESE PIN (2026-09-25, the Korean test page 001)
Our own prompt turned the panel-clipped fragment `1이르다리고이느` into "One, get up and
move!" — fluent English asserting what the source cannot support. Upstream's prompt (which
the engine already translates every other line of an engine job with) answers that case with
one rule: unreadable or already-in-target text comes back AS IT IS. So:

  * the system/user prompts must carry upstream's rules, verbatim enough to be the same ask;
  * a line the model echoed must be recognised as a deliberate answer (not retried, no
    invented English, the artwork left alone);
  * and the SECOND PASS must never drop the engine's own region for a recovered read that
    produced no translation — that would silently lose the English upstream already had
    (the v0.30.1 failure), which is worse than leaving the source in place.
"""
from __future__ import annotations

import httpx

import app.pipeline.second_pass as second_pass
import app.pipeline.translate as translate
from app.pipeline.types import TextBlock


# ------------------------------------------------------------------ the prompt itself

def test_user_prompt_carries_upstreams_gibberish_rule():
    """Upstream's _PROMPT_TEMPLATE, adapted to our numbering — the rule that does the work."""
    prompt = translate._PROMPT_TEMPLATE.format(lang=translate.TARGET_LANG)
    assert "translate the following text from a manga to English" in prompt
    assert "looks like gibberish you have to output it as it is instead" in prompt
    assert "Keep the 'N.' numbering format" in prompt


def test_target_language_is_english_never_the_source(monkeypatch):
    """REGRESSION: `{to_lang}` is the TARGET, not the source.

    Upstream's clause the other way round — "if it's already in {to_lang}" with the SOURCE
    language in it — makes the model hand text back untranslated. Measured on the whole corpus
    (2026-09-25): 15.1% of degraded Korean reads and 8.3% of CLEAN Japanese lines came back as
    the source (ja control GAP 4.1% -> 9.1%). The source is named separately, in `## Source`.
    """
    sent = {}

    def fake_post(*args, **kwargs):
        sent["messages"] = kwargs["json"]["messages"]

        class _R:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "1. Hello"}}], "usage": {}}

        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    translate.translate_lines(["안녕하세요"], "m", "k", "https://x/v1", source_lang="ko")
    msgs = sent["messages"]
    system = msgs[0]["content"]
    user = msgs[-1]["content"]
    assert "from a manga to English" in user
    assert "already in English" in user
    assert "already in Korean" not in user
    assert "sound natural in English" in system
    assert "The text to translate is Korean" in system


def test_request_carries_upstreams_few_shot_example(monkeypatch):
    """Upstream's request shape, kept whole: system, their example, then the work.

    Their `common_gpt._assemble_request` sends a target-language example before the prompt, and
    the engine this app draws for uses the same request — so ours matches it. (Measured
    2026-09-25: the app's path translates clean dialogue the same with or without the sample;
    it is kept for fidelity with the engine's own translations, not because it rescues
    anything.)
    """
    sent = {}

    def fake_post(*args, **kwargs):
        sent["messages"] = kwargs["json"]["messages"]

        class _R:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "1. Hello"}}], "usage": {}}

        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    translate.translate_lines(["こんにちは"], "m", "k", "https://x/v1", source_lang="ja")
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "恥ずかしい" in sent["messages"][1]["content"]
    assert "I'm embarrassed" in sent["messages"][2]["content"]


def test_system_prompt_is_upstreams_method():
    sys_p = translate._system_prompt("ja")
    assert "Professional Comic Translator" in sys_p
    # the three steps of their method, and the rules that keep an unreadable line honest
    assert "LITERAL TRANSLATION" in sys_p and "REFINEMENT" in sys_p
    assert "Leave ambiguous elements as they are without interpretation" in sys_p
    assert "do not add pronouns that do not exist in the original text" in sys_p
    assert "Preserve original gibberish and sound effects without translation" in sys_p
    # their format line, adapted
    assert "Output each line as 'N. <translation>'" in sys_p


def test_system_prompt_keeps_our_two_render_side_additions():
    sys_p = translate._system_prompt("ja")
    assert "Fitting the bubble" in sys_p          # the typesetter draws inside a balloon
    assert "name separate from their spoken line" in sys_p
    assert "Korean register" not in sys_p
    ko = translate._system_prompt("ko")
    assert "Korean register" in ko and "banmal" in ko


def test_no_anti_invention_scaffolding_left():
    """The marker/abstention experiment is gone: no markers, no '[[partial source]]'."""
    for s in (translate.SYSTEM_PROMPT, translate.SYSTEM_PROMPT_KO, translate._PROMPT_TEMPLATE):
        assert "[[partial source]]" not in s
        assert "anti-invention" not in s.lower()


# ------------------------------------------------------------------ echoed source

def test_echoed_source_is_recognised():
    # a Korean read handed straight back — upstream's answer for unreadable text
    assert translate._echoed_source("1이르다리고이느")
    assert translate._echoed_source("  대한민국매출규모 ")
    # ...even though ASCII-stripping would leave the stray digit "1" behind
    assert translate._normalize_ascii("1이르다리고이느") == "1"
    # real translations are not echoes
    assert not translate._echoed_source("One, get up and move!")
    assert not translate._echoed_source("Hankyung Department Store")
    assert not translate._echoed_source("")
    # a plain-ASCII answer is never an echo (nothing was echoed back at all)
    assert not translate._echoed_source("1")
    # a refusal marker is a different thing: it still gets the per-line retry
    assert not translate._echoed_source("[TEXT UNTRANSLATABLE]")


def test_echoed_line_is_not_retried_and_stays_untranslated(monkeypatch):
    """One request only: an echoed source is a final answer, not a dropped line."""
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            # line 2 comes back as the source, exactly as the degraded read does
            return {"choices": [{"message": {"content": "1. Hello\n2. 1이르다리고이느\n"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    blocks = [TextBlock(bbox=(0, 0, 10, 10), text="こんにちは", orientation="vertical"),
              TextBlock(bbox=(0, 20, 10, 10), text="1이르다리고이느", orientation="vertical")]
    out, _pt, _ct = translate.translate_page(blocks, "m", "k", "https://x/v1", source_lang="ko")
    assert out[0].translation == "Hello"
    assert out[1].translation == ""          # left alone, no invented English
    assert calls["n"] == 1                   # no per-line retry for the echoed line


def test_dropped_line_still_gets_its_retry(monkeypatch):
    """A line the model simply omitted is still chased — only echoes are not."""
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"choices": [{"message": {"content": "1. Hello\n"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            return {"choices": [{"message": {"content": "1. Second line\n"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 3}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    blocks = [TextBlock(bbox=(0, 0, 10, 10), text="こんにちは", orientation="vertical"),
              TextBlock(bbox=(0, 20, 10, 10), text="世界", orientation="vertical")]
    out, _pt, _ct = translate.translate_page(blocks, "m", "k", "https://x/v1", source_lang="ja")
    assert out[1].translation == "Second line"
    assert calls["n"] == 2


# ------------------------------------------------------------------ second pass fallback

ENGINE_REGION = {"x0": 145, "y0": 1534, "x1": 398, "y1": 1572,
                 "text": "대한민국매출규모", "translation": "South Korea sales scale"}


def _ours_overlapping_plate() -> list[TextBlock]:
    """Our read of the whole plate: it covers two lines, one of them clipped/unreadable."""
    return [TextBlock(bbox=(145, 1534, 300, 120), text="대한민국매출규모 1이르다리고이느",
                      confidence=0.8, orientation="horizontal")]


def test_overlapping_read_never_touches_the_engines_region(monkeypatch):
    """The engine owns its own boxes: a recovered box that touches one changes NOTHING.

    This is the v0.30.6 rule, and it closes three shipped failures at once — v0.30.1 dropped
    the engine's region and the plate lost its English, v0.30.2 re-translated the same text
    and duplicated it, and v0.30.5 lettered romanised garbage ("South Korea sales scale
    1ireudarigoineu") over the engine's own clean read.
    """
    monkeypatch.setattr(second_pass, "mem_available_gb", lambda: 8.0)
    monkeypatch.setattr(second_pass, "_ours", lambda path, lang: _ours_overlapping_plate())
    called = {"translate": 0}

    def fake_translate_page(blocks, *a, **k):
        called["translate"] += 1
        for b in blocks:
            b.translation = "South Korea is number one in sales"
        return blocks, 0, 0

    monkeypatch.setattr(translate, "translate_page", fake_translate_page)
    out, drop, why = second_pass.augment_regions(
        "/tmp/none.png", "ko", [dict(ENGINE_REGION)], model="m", api_key="k",
        base_url="https://x/v1")
    assert out == []                       # nothing added
    assert drop == set()                   # and the engine's region survives untouched
    assert called["translate"] == 0        # the overlapping read is not translated at all
    assert "0 missed" in why and "covered" in why


def test_standalone_miss_is_added_when_translatable(monkeypatch):
    """A box touching NO engine region is the case this pass exists for."""
    monkeypatch.setattr(second_pass, "mem_available_gb", lambda: 8.0)
    monkeypatch.setattr(second_pass, "_ours", lambda path, lang: [
        TextBlock(bbox=(145, 1700, 300, 40), text="1위를 다리고 이는",
                  confidence=0.8, orientation="horizontal")])

    def fake_translate_page(blocks, *a, **k):
        for b in blocks:
            b.translation = "the one running for first place"
        return blocks, 0, 0

    monkeypatch.setattr(translate, "translate_page", fake_translate_page)
    out, drop, why = second_pass.augment_regions(
        "/tmp/none.png", "ko", [dict(ENGINE_REGION)], model="m", api_key="k",
        base_url="https://x/v1")
    assert drop == set()
    assert len(out) == 1
    assert out[0]["translation"] == "the one running for first place"
    assert out[0]["pass"] == "second"
    assert "1 standalone" in why


def test_standalone_unreadable_miss_is_dropped_not_lettered(monkeypatch):
    monkeypatch.setattr(second_pass, "mem_available_gb", lambda: 8.0)
    monkeypatch.setattr(second_pass, "_ours", lambda path, lang: [
        TextBlock(bbox=(10, 10, 80, 30), text="1이르다리고이느", confidence=0.7,
                  orientation="horizontal")])

    def fake_translate_page(blocks, *a, **k):
        for b in blocks:
            b.translation = ""      # upstream's rule: unreadable text comes back as the source
        return blocks, 0, 0

    monkeypatch.setattr(translate, "translate_page", fake_translate_page)
    out, drop, _why = second_pass.augment_regions(
        "/tmp/none.png", "ko", [], model="m", api_key="k", base_url="https://x/v1")
    assert out == [] and drop == set()
