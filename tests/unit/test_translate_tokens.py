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
