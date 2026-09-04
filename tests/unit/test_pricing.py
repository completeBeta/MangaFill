"""Peak/off-peak pricing — cost from token usage."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.pricing import compute_cost, is_offpeak


class _M:
    def __init__(self, price_in=2.0, price_out=8.0, offpeak_in=None, offpeak_out=None,
                 offpeak_start=None, offpeak_end=None):
        self.price_in = price_in
        self.price_out = price_out
        self.offpeak_in = offpeak_in
        self.offpeak_out = offpeak_out
        self.offpeak_start = offpeak_start
        self.offpeak_end = offpeak_end


def test_flat_peak_cost():
    m = _M(price_in=1.0, price_out=2.0)
    # 1M in + 1M out = $3
    assert compute_cost(m, 1_000_000, 1_000_000) == 3.0
    # no off-peak window -> peak always
    assert is_offpeak(m) is False


def test_offpeak_discount():
    m = _M(price_in=2.0, price_out=8.0, offpeak_in=1.0, offpeak_out=4.0,
           offpeak_start="00:30", offpeak_end="16:30")
    # inside window (12:00 UTC) -> off-peak rates
    at_noon = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert is_offpeak(m, at_noon) is True
    assert compute_cost(m, 1_000_000, 1_000_000, at_noon) == 5.0  # 1.0 + 4.0

    # outside window (20:00 UTC) -> peak rates
    at_peak = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    assert is_offpeak(m, at_peak) is False
    assert compute_cost(m, 1_000_000, 1_000_000, at_peak) == 10.0  # 2.0 + 8.0


def test_wraps_midnight():
    m = _M(price_in=2.0, price_out=8.0, offpeak_in=1.0, offpeak_out=4.0,
           offpeak_start="16:30", offpeak_end="00:30")
    # 23:00 UTC is inside (>= 16:30)
    assert is_offpeak(m, datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc)) is True
    # 00:15 UTC is inside (< 00:30)
    assert is_offpeak(m, datetime(2026, 9, 2, 0, 15, tzinfo=timezone.utc)) is True
    # 10:00 UTC is outside
    assert is_offpeak(m, datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)) is False


def test_no_pricing_configured():
    m = _M(price_in=0.0, price_out=0.0)
    assert compute_cost(m, 1_000_000, 1_000_000) == 0.0
