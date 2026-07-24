"""Dated-futures continuous-contract engine (track T4 / roadmap P4).

Distinct from the perp ``FundingModel`` (perps never expire): dated futures
expire, so a multi-year backtest must ROLL from the expiring contract to the
next and STITCH them into one continuous series. This builds that series from
per-delivery-month OHLCV with a date-based roll calendar and ratio
(multiplicative) back-adjustment — the positivity-preserving standard, so the
stitched bars still satisfy the OHLCVBar validators (panama/additive
adjustment can go negative; noted but not the default).

Look-ahead safety: the roll happens ``roll_days_before`` days before expiry
using only prices available at the roll date, and the adjustment is applied to
HISTORY (bars at/older than the roll) — never forward. The current (front)
contract's bars are the real, unadjusted prices.

Buildable + testable now on synthetic multi-contract fixtures; real
per-delivery-month history is feed-gated (yfinance only serves the continuous
front-month), so wiring live data is deferred until the operator provisions a
futures-chain vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from tradingagents.contracts import OHLCVBar


@dataclass(frozen=True)
class FuturesContract:
    """One delivery month: its root, expiry date, and own OHLCV series
    (chronological, ascending by start)."""

    root: str
    expiry: date
    bars: list[OHLCVBar]


@dataclass(frozen=True)
class RollEvent:
    roll_date: date
    from_expiry: date
    to_expiry: date
    front_close: float   # expiring contract's close at the roll
    next_close: float    # next contract's close at the roll
    ratio: float         # next_close / front_close (the back-adjust factor)


def _close_as_of(contract: FuturesContract, when: date) -> float | None:
    """Close of the last bar at/or before ``when`` (None if the contract has no
    such bar — look-ahead-safe: never uses a future bar)."""
    close = None
    for bar in contract.bars:
        if bar.start.date() <= when:
            close = bar.close
        else:
            break
    return close


def _scaled(bar: OHLCVBar, factor: float) -> OHLCVBar:
    """Ratio-adjust one bar; a single positive factor preserves OHLC ordering
    and positivity, so the result still validates."""
    return OHLCVBar(
        timeframe=bar.timeframe, start=bar.start,
        open=bar.open * factor, high=bar.high * factor,
        low=bar.low * factor, close=bar.close * factor, volume=bar.volume)


def stitch_continuous(
    contracts: list[FuturesContract],
    *,
    roll_days_before: int = 5,
) -> tuple[list[OHLCVBar], list[RollEvent]]:
    """Stitch dated contracts into one continuous, ratio-back-adjusted series.

    Each contract is active from the prior roll date until its own roll date
    (``expiry - roll_days_before``); the last contract runs to the end of its
    bars. History is scaled by the cumulative next/front close ratio at each
    roll so there is no price jump. Returns ``(continuous_bars, roll_events)``.
    A single contract passes through unchanged with an empty roll log.
    """
    if not contracts:
        return [], []
    ordered = sorted(contracts, key=lambda c: c.expiry)
    if len(ordered) == 1:
        return list(ordered[0].bars), []

    roll_dates = [c.expiry - timedelta(days=roll_days_before)
                  for c in ordered[:-1]]

    # roll events + per-segment cumulative ratio (newest segment factor = 1)
    events: list[RollEvent] = []
    ratios: list[float] = []  # ratio at roll k (front→next)
    for k in range(len(ordered) - 1):
        rd = roll_dates[k]
        front = _close_as_of(ordered[k], rd)
        nxt = _close_as_of(ordered[k + 1], rd)
        ratio = (nxt / front) if (front and nxt and front > 0) else 1.0
        ratios.append(ratio)
        events.append(RollEvent(
            roll_date=rd, from_expiry=ordered[k].expiry,
            to_expiry=ordered[k + 1].expiry,
            front_close=front or 0.0, next_close=nxt or 0.0, ratio=ratio))

    # segment k active window: (prev_roll, roll_k]; last segment: (prev_roll, end]
    out: list[OHLCVBar] = []
    for k, contract in enumerate(ordered):
        seg_start = roll_dates[k - 1] if k > 0 else date.min
        seg_end = roll_dates[k] if k < len(ordered) - 1 else date.max
        # cumulative back-adjust factor for this (and older) segment: product of
        # the ratios at all rolls from here forward
        factor = 1.0
        for j in range(k, len(ratios)):
            factor *= ratios[j]
        for bar in contract.bars:
            d = bar.start.date()
            if seg_start < d <= seg_end:
                out.append(_scaled(bar, factor) if factor != 1.0 else bar)
    out.sort(key=lambda b: b.start)
    return out, events


__all__ = ["FuturesContract", "RollEvent", "stitch_continuous"]
