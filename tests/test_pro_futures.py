"""Dated-futures continuous-contract engine (track T4): date-based roll +
ratio back-adjustment stitches contracts into one jump-free, positive,
look-ahead-safe series."""

from datetime import timedelta

import pytest

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.pro.backtest import (
    FuturesContract,
    stitch_continuous,
)

_START = BASE_TS.date()


def _bar(day: int, price: float) -> OHLCVBar:
    return OHLCVBar(timeframe=Timeframe.D1,
                    start=BASE_TS + timedelta(days=day),
                    open=price, high=price + 0.5, low=price - 0.5,
                    close=price, volume=1000.0)


def _contract(root: str, expiry_day: int, days: range, price: float) -> FuturesContract:
    return FuturesContract(root=root, expiry=_START + timedelta(days=expiry_day),
                           bars=[_bar(d, price) for d in days])


def test_single_contract_passthrough():
    c = _contract("GC", 20, range(0, 21), 100.0)
    bars, rolls = stitch_continuous([c])
    assert rolls == []
    assert [b.close for b in bars] == [b.close for b in c.bars]


def test_two_contracts_stitch_continuously():
    # front ~100 expiring day 20 (roll day 15); next ~110 expiring day 40
    front = _contract("GC", 20, range(0, 21), 100.0)
    back = _contract("GC", 40, range(0, 41), 110.0)
    bars, rolls = stitch_continuous([front, back], roll_days_before=5)

    assert len(rolls) == 1
    assert rolls[0].roll_date == _START + timedelta(days=15)
    assert rolls[0].ratio == pytest.approx(1.1)  # 110 / 100

    # front segment (day ≤ 15) is back-adjusted ×1.1 → ~110; back segment raw
    by_day = {b.start.date(): b.close for b in bars}
    assert by_day[_START + timedelta(days=0)] == pytest.approx(110.0)   # A×1.1
    assert by_day[_START + timedelta(days=15)] == pytest.approx(110.0)  # A×1.1
    assert by_day[_START + timedelta(days=40)] == pytest.approx(110.0)  # B raw

    # continuity: no price jump anywhere in the stitched series
    closes = [b.close for b in bars]
    assert max(closes) - min(closes) == pytest.approx(0.0, abs=1e-6)


def test_back_segment_is_unadjusted_and_positive():
    front = _contract("GC", 20, range(0, 21), 50.0)
    back = _contract("GC", 40, range(0, 41), 40.0)  # backwardation: next lower
    bars, rolls = stitch_continuous([front, back], roll_days_before=5)
    assert rolls[0].ratio == pytest.approx(0.8)  # 40 / 50
    # the current (back) contract's real prices are untouched
    tail = [b for b in bars if b.start.date() > _START + timedelta(days=15)]
    assert all(b.close == pytest.approx(40.0) for b in tail)
    # every stitched bar stays strictly positive (ratio adjustment is safe)
    assert all(b.low > 0 and b.close > 0 for b in bars)


def test_no_lookahead_uses_only_roll_date_prices():
    # a price spike AFTER the roll must not change the roll ratio
    front = _contract("GC", 20, range(0, 21), 100.0)
    back_bars = [_bar(d, 110.0) for d in range(0, 16)] + \
                [_bar(d, 999.0) for d in range(16, 41)]  # spike after roll (day15)
    back = FuturesContract("GC", _START + timedelta(days=40), back_bars)
    _, rolls = stitch_continuous([front, back], roll_days_before=5)
    assert rolls[0].ratio == pytest.approx(1.1)  # uses day-15 close (110), not 999
