"""Cost estimation from token usage + a model's peak/off-peak pricing.

OpenAI-compatible APIs return `usage.prompt_tokens` / `usage.completion_tokens`
but NOT a dollar cost, so the dashboard estimates cost from those counts against
the per-model rates ($/1M tokens). Peak vs off-peak follows each model's own
off-peak window (UTC), like DeepSeek's discounted off-peak hours.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Model


def _hm_to_min(s: str | None) -> int | None:
    """Parse 'HH:MM' to minutes-since-midnight; None if empty/unparseable."""
    if not s:
        return None
    try:
        h, m = str(s).strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def is_offpeak(model: Model | None, now: datetime | None = None) -> bool:
    """True if `now` (UTC) is inside the model's configured off-peak window."""
    if model is None:
        return False
    start = _hm_to_min(model.offpeak_start)
    end = _hm_to_min(model.offpeak_end)
    if start is None or end is None or start == end:
        return False
    now = now or datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # window wraps midnight


def compute_cost(model: Model | None, prompt_tokens: int, completion_tokens: int,
                 now: datetime | None = None) -> float:
    """USD cost for the token counts, using peak/off-peak rates ($/1M tokens)."""
    if model is None:
        return 0.0
    off = is_offpeak(model, now)
    in_rate = (model.offpeak_in if off and model.offpeak_in is not None else model.price_in) or 0.0
    out_rate = (model.offpeak_out if off and model.offpeak_out is not None else model.price_out) or 0.0
    return (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000.0
