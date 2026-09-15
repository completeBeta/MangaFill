"""Translation-provider resilience: typed errors, retries, and tunable timeout.

Regression cover for the outage that looked like a stall: a degraded endpoint
answering 200 with an empty body surfaced as a bare `KeyError: 'choices'`, the
job kept running, and nothing told the user. Now every failure is a
`ProviderError` naming the endpoint, transient errors are retried, and the
timeout is configurable (lax by default).
"""
import pytest

from app.pipeline import translate


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _ok(content="1. Hello", pt=5, ct=2):
    return _Resp(200, {"choices": [{"message": {"content": content}}],
                       "usage": {"prompt_tokens": pt, "completion_tokens": ct}})


def test_missing_choices_raises_named_provider_error(monkeypatch):
    monkeypatch.setattr(translate.httpx, "post",
                        lambda *a, **k: _Resp(200, {"error": {"message": "upstream down"}}))
    with pytest.raises(translate.ProviderError) as ei:
        translate.translate_lines(["こんにちは"], "m", "k", "https://api.deepseek.com/v1",
                                  max_retries=0)
    msg = str(ei.value)
    assert "api.deepseek.com" in msg          # names the endpoint, not just 'choices'
    assert "no completion" in msg
    assert "upstream down" in msg
    assert ei.value.transient is True


def test_none_first_choice_raises(monkeypatch):
    monkeypatch.setattr(translate.httpx, "post",
                        lambda *a, **k: _Resp(200, {"choices": [None]}))
    with pytest.raises(translate.ProviderError):
        translate.translate_lines(["x"], "m", "k", "https://x/v1", max_retries=0)


def test_permanent_http_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(401, None, "unauthorized")

    monkeypatch.setattr(translate.httpx, "post", post)
    with pytest.raises(translate.ProviderError) as ei:
        translate.translate_lines(["x"], "m", "k", "https://x/v1", max_retries=3)
    assert ei.value.status == 401
    assert ei.value.transient is False
    assert calls["n"] == 1  # a bad key is pointless to retry


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(translate.time, "sleep", lambda _s: None)
    seq = [_Resp(503, None, "busy"), _ok()]

    def post(*a, **k):
        return seq.pop(0)

    monkeypatch.setattr(translate.httpx, "post", post)
    out, pt, ct = translate.translate_lines(["こんにちは"], "m", "k", "https://x/v1",
                                            max_retries=2)
    assert out == ["Hello"]
    assert (pt, ct) == (5, 2)


def test_transient_error_gives_up_and_raises(monkeypatch):
    monkeypatch.setattr(translate.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(502, None, "bad gateway")

    monkeypatch.setattr(translate.httpx, "post", post)
    with pytest.raises(translate.ProviderError):
        translate.translate_lines(["x"], "m", "k", "https://x/v1", max_retries=2)
    assert calls["n"] == 3  # initial + 2 retries


def test_timeout_default_is_lax_and_overridable(monkeypatch):
    seen = {}

    def post(url, **k):
        seen["timeout"] = k.get("timeout")
        return _ok()

    monkeypatch.setattr(translate.httpx, "post", post)
    translate.translate_lines(["a"], "m", "k", "https://x/v1", max_retries=0)
    assert seen["timeout"] == translate.DEFAULT_TIMEOUT == 300.0

    translate.translate_lines(["a"], "m", "k", "https://x/v1", timeout=42, max_retries=0)
    assert seen["timeout"] == 42


def test_timeout_exception_is_transient(monkeypatch):
    def post(*a, **k):
        raise translate.httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(translate.httpx, "post", post)
    with pytest.raises(translate.ProviderError) as ei:
        translate.translate_lines(["x"], "m", "k", "https://x/v1", max_retries=0)
    assert "timed out" in str(ei.value)
    assert ei.value.transient is True


def test_deepseek_still_disables_reasoning(monkeypatch):
    seen = {}

    def post(url, **k):
        seen.update(k.get("json") or {})
        return _ok()

    monkeypatch.setattr(translate.httpx, "post", post)
    translate.translate_lines(["a"], "m", "k", "https://api.deepseek.com/v1", max_retries=0)
    assert seen["thinking"] == {"type": "disabled"}


def test_llm_settings_have_lax_defaults():
    from app.settings_store import SETTINGS

    assert SETTINGS["llm_timeout"][0] == "300"
    assert SETTINGS["llm_max_retries"][0] == "2"
    assert SETTINGS["provider_fail_limit"][0] == "3"
