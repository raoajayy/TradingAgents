"""Gold options / implied-vol context (P3-09) — free sources only.

Deribit lists NO gold options, and there is no free gold options chain
(CME settlement files are paywalled), so this module is honest about what
free data can support:

- **GVZ level / 1d change** stay in ``positioning.GoldVolFeed`` (DR-1);
  this module deliberately does not duplicate them.
- **GOLD_IV_RANK / GOLD_IV_PERCENTILE**: where today's GVZ close sits in
  its own trailing ~252-session window — rank is min-max position,
  percentile is the share of closes at or below today's. Both need a
  minimum history (``min_obs``) and are simply omitted below it.
- **GOLD_IV_RV_SPREAD**: GVZ (30-day implied vol, % annualized) minus the
  20-day Parkinson realized vol of gold futures, annualized to the same
  units. This is a *vol-risk-premium proxy*, NOT a term structure and NOT
  an options surface — one implied point against one realized estimator.
  Positive = options price more movement than gold recently delivered.

All series come through the injectable ``YFinanceDailyBarsFeed`` loader
(``^GVZ`` and ``GC=F``), so tests stub frames and no transport is touched.

Dashboard note: the intel snapshot exposes these readings both in the flat
``metrics`` list and as a compact ``gold_vol_context`` block (see
``dashboard/intel.py``) so a future vol-context tile can render without a
new endpoint. The metric names are registered in ``METRIC_INFO``, which
makes them targetable by P2-07 condition alerts automatically.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence

from tradingagents.contracts import MetricReading, Timeframe

GVZ_SYMBOL = "^GVZ"
GOLD_FUTURES_SYMBOL = "GC=F"
IV_WINDOW = 252          # trailing sessions for rank/percentile (~1y)
IV_MIN_OBS = 60          # below this, rank/percentile are noise — omit
RV_WINDOW = 20           # Parkinson realized-vol window (sessions)
TRADING_DAYS = 252
_PARKINSON_COEF = 1.0 / (4.0 * math.log(2.0))


def iv_rank(closes: Sequence[float], window: int = IV_WINDOW,
            min_obs: int = IV_MIN_OBS) -> float | None:
    """Min-max position of the last value inside its trailing window,
    0-100. None on short history or a flat window (rank undefined)."""
    tail = list(closes)[-window:]
    if len(tail) < min_obs:
        return None
    lo, hi = min(tail), max(tail)
    if hi == lo:
        return None
    return 100.0 * (tail[-1] - lo) / (hi - lo)


def iv_percentile(closes: Sequence[float], window: int = IV_WINDOW,
                  min_obs: int = IV_MIN_OBS) -> float | None:
    """Share (0-100) of trailing-window values at or below the last value
    (inclusive of the last value itself). None on short history."""
    tail = list(closes)[-window:]
    if len(tail) < min_obs:
        return None
    last = tail[-1]
    return 100.0 * sum(1 for v in tail if v <= last) / len(tail)


def parkinson_vol_annualized(bars, window: int = RV_WINDOW) -> float | None:
    """Parkinson (1980) high-low realized vol over the last ``window``
    bars, annualized and scaled to percent — the same units GVZ quotes.
    None on short history or degenerate (non-positive / inverted) ranges."""
    tail = list(bars)[-window:]
    if len(tail) < window:
        return None
    terms = []
    for bar in tail:
        if bar.low <= 0 or bar.high < bar.low:
            return None
        if bar.high == bar.low:
            terms.append(0.0)
        else:
            terms.append(math.log(bar.high / bar.low) ** 2)
    daily_var = _PARKINSON_COEF * sum(terms) / len(terms)
    return math.sqrt(daily_var * TRADING_DAYS) * 100.0


class GoldVolContextFeed:
    """IV rank/percentile of GVZ plus the GVZ-vs-realized spread proxy.

    Complements (never duplicates) ``positioning.GoldVolFeed``: that feed
    owns GOLD_VOL_INDEX / _CHANGE_1D; this one owns the derived context.
    Sub-metrics degrade independently — short GVZ history drops rank and
    percentile, a failing GC=F fetch drops only the spread. No GVZ bars at
    all raises, so the builder discloses the whole feed as missing.
    """

    name = "gold_vol_context"

    def __init__(self, bars_feed, window: int = IV_WINDOW,
                 rv_window: int = RV_WINDOW, min_obs: int = IV_MIN_OBS):
        if rv_window < 2:
            raise ValueError("rv_window must be >= 2")
        self._bars = bars_feed  # YFinanceDailyBarsFeed (injectable loader)
        self._window = window
        self._rv_window = rv_window
        self._min_obs = min_obs

    @classmethod
    def probe(cls, timeout: float = 8.0) -> bool:
        if os.environ.get("PRO_DISABLE_LIVE_VENDORS") == "1":
            return False
        try:
            from tradingagents.pro.ingestion.gold_feeds import YFinanceDailyBarsFeed

            return bool(cls(YFinanceDailyBarsFeed()).get_metrics())
        except Exception:
            return False

    def get_metrics(self) -> list[MetricReading]:
        gvz_bars = self._bars.get_bars(GVZ_SYMBOL, Timeframe.D1,
                                       limit=self._window)
        if not gvz_bars:
            raise ValueError("no GVZ bars returned")
        closes = [b.close for b in gvz_bars]
        as_of = gvz_bars[-1].start
        readings: list[MetricReading] = []

        rank = iv_rank(closes, self._window, self._min_obs)
        if rank is not None:
            readings.append(MetricReading(
                name="GOLD_IV_RANK", value=rank, unit="pct",
                as_of=as_of, source=self.name))
        percentile = iv_percentile(closes, self._window, self._min_obs)
        if percentile is not None:
            readings.append(MetricReading(
                name="GOLD_IV_PERCENTILE", value=percentile, unit="pct",
                as_of=as_of, source=self.name))

        realized = None
        try:
            gold_bars = self._bars.get_bars(GOLD_FUTURES_SYMBOL, Timeframe.D1,
                                            limit=self._rv_window)
            realized = parkinson_vol_annualized(gold_bars, self._rv_window)
        except Exception:
            realized = None  # spread degrades alone; rank/percentile serve
        if realized is not None:
            readings.append(MetricReading(
                name="GOLD_IV_RV_SPREAD", value=closes[-1] - realized,
                unit="vol_points", as_of=as_of, source=self.name))
        return readings
